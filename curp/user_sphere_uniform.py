import torch
import torch.nn as nn
from tqdm import tqdm


class PQCodebookModel(nn.Module):
    def __init__(self, codebook_path, device="cpu"):
        super().__init__()
        checkpoint = torch.load(codebook_path, map_location=device)
        if "codebooks" not in checkpoint:
            raise ValueError(f"Checkpoint must contain 'codebooks' key. Found keys: {checkpoint.keys()}")
        codebooks_list = []
        for cb in checkpoint["codebooks"]:
            if isinstance(cb, torch.Tensor):
                codebooks_list.append(nn.Parameter(cb.to(device), requires_grad=False))
            else:
                codebooks_list.append(nn.Parameter(torch.tensor(cb, device=device), requires_grad=False))
        self.codebooks = nn.ParameterList(codebooks_list)
        self.num_subspaces = checkpoint.get("num_subspaces", len(self.codebooks))
        self.subspace_dim = checkpoint.get("subspace_dim", self.codebooks[0].shape[1] if self.codebooks else None)
        self.codebook_size = self.codebooks[0].shape[0]
        self.emb_dim = self.num_subspaces * self.subspace_dim


def embeddings_from_indices(pq_codebook_model, indices):
    batch_size, his_len, num_subspaces = indices.shape
    if num_subspaces != pq_codebook_model.num_subspaces:
        raise ValueError("indices num_subspaces does not match codebook")
    subspace_dim = pq_codebook_model.subspace_dim
    parts = []
    for i, codebook in enumerate(pq_codebook_model.codebooks):
        cb = codebook.to(indices.device)
        flat_idx = indices[:, :, i].reshape(-1)
        picked = cb[flat_idx].reshape(batch_size, his_len, subspace_dim)
        parts.append(picked)
    return torch.cat(parts, dim=-1)


def user_vector_from_indices(pq_codebook_model, indices, reduce="mean"):
    """
    indices: LongTensor [batch, his_len, num_subspaces]
    returns: FloatTensor [batch, emb_dim]
    """
    embs = embeddings_from_indices(pq_codebook_model, indices)
    if reduce == "mean":
        return embs.mean(dim=1)
    if reduce == "sum":
        return embs.sum(dim=1)
    if reduce == "flatten":
        return embs.reshape(embs.size(0), -1)
    raise ValueError(f"Unknown reduce: {reduce}")


def sample_uniform_sphere(n, device=None, eps=1e-8):
    """
    Returns [n,3] roughly uniform on the unit sphere.
    """
    x = torch.randn(n, 3, device=device)
    return x / torch.norm(x, dim=1, keepdim=True).clamp_min(eps)


def mmd_rbf(x, y, sigma=0.5):
    """
    Maximum Mean Discrepancy with RBF kernel.
    x: [n,3], y: [m,3]
    """
    x2 = (x * x).sum(dim=1, keepdim=True)
    y2 = (y * y).sum(dim=1, keepdim=True)
    xy = x @ y.t()
    dist_xx = x2 + x2.t() - 2 * (x @ x.t())
    dist_yy = y2 + y2.t() - 2 * (y @ y.t())
    dist_xy = x2 + y2.t() - 2 * xy
    k_xx = torch.exp(-dist_xx / (2 * sigma * sigma))
    k_yy = torch.exp(-dist_yy / (2 * sigma * sigma))
    k_xy = torch.exp(-dist_xy / (2 * sigma * sigma))
    return k_xx.mean() + k_yy.mean() - 2 * k_xy.mean()


def fit_uniform_sphere_map(user_vectors, steps=800, lr=1e-2, batch_size=512, sigma=0.5, seed=0):
    """
    Learn a linear map W to project user vectors into 3D so that
    the normalized outputs are close to uniform on the unit sphere.
    Returns dict with W and mean.
    """
    if user_vectors.dim() != 2:
        raise ValueError("user_vectors must be 2D [n, d]")
    device = user_vectors.device
    torch.manual_seed(seed)
    n, d = user_vectors.shape
    mean = user_vectors.mean(dim=0, keepdim=True)
    x = user_vectors - mean
    # Initialize with PCA-like projection
    u, s, v = torch.linalg.svd(x, full_matrices=False)
    w = nn.Parameter(v[:3].clone())
    optimizer = torch.optim.Adam([w], lr=lr)

    for step in tqdm(range(steps), desc="fit_uniform_sphere_map"):
        idx = torch.randint(0, n, (min(batch_size, n),), device=device)
        xb = x[idx]
        proj = xb @ w.t()
        proj = proj / torch.norm(proj, dim=1, keepdim=True).clamp_min(1e-8)
        target = sample_uniform_sphere(proj.size(0), device=device)
        loss = mmd_rbf(proj, target, sigma=sigma)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # keep projection stable
        with torch.no_grad():
            w[:] = torch.nn.functional.normalize(w, dim=1)

    return {"mean": mean.squeeze(0), "W": w.detach()}


def fit_uniform_sphere_map_from_sampler(sample_fn, emb_dim, steps=800, lr=1e-2, batch_size=512,
                                        sigma=0.5, seed=0, mean_estimate_batches=50):
    """
    Train W without materializing all user_vectors in memory.
    sample_fn(batch_size) -> FloatTensor [batch_size, emb_dim]
    """
    torch.manual_seed(seed)
    # Estimate mean with a few batches
    mean = torch.zeros(emb_dim, device=sample_fn(1).device)
    total = 0
    for _ in range(mean_estimate_batches):
        xb = sample_fn(batch_size)
        mean += xb.sum(dim=0)
        total += xb.size(0)
    mean = mean / max(total, 1)

    w = nn.Parameter(torch.randn(3, emb_dim, device=mean.device))
    w.data = torch.nn.functional.normalize(w.data, dim=1)
    optimizer = torch.optim.Adam([w], lr=lr)

    for _ in tqdm(range(steps), desc="fit_uniform_sphere_map_from_sampler"):
        xb = sample_fn(batch_size)
        xb = xb - mean
        proj = xb @ w.t()
        proj = proj / torch.norm(proj, dim=1, keepdim=True).clamp_min(1e-8)
        target = sample_uniform_sphere(proj.size(0), device=proj.device)
        loss = mmd_rbf(proj, target, sigma=sigma)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            w[:] = torch.nn.functional.normalize(w, dim=1)

    return {"mean": mean.detach(), "W": w.detach()}


def project_to_uniform_sphere(user_vectors, state, eps=1e-8):
    """
    user_vectors: [n,d], state: output from fit_uniform_sphere_map
    returns: [n,3] on unit sphere
    """
    mean = state["mean"]
    w = state["W"]
    x = user_vectors - mean
    coords = x @ w.t()
    return coords / torch.norm(coords, dim=1, keepdim=True).clamp_min(eps)


def indices_to_uniform_sphere_points(pq_codebook_model, indices, state, reduce="mean"):
    """
    indices: LongTensor [batch, his_len, num_subspaces]
    returns: FloatTensor [batch, 3] on unit sphere
    """
    user_vecs = user_vector_from_indices(pq_codebook_model, indices, reduce=reduce)
    return project_to_uniform_sphere(user_vecs, state)

import os
import json
import argparse
import torch
import torch.nn as nn
from flask import Flask, request, render_template, redirect, url_for
from transformers import AutoModelForCausalLM, AutoTokenizer
from user_sphere_uniform import project_to_uniform_sphere


def ensure_chat_template(tokenizer, model_path):
    if hasattr(tokenizer, "chat_template") and tokenizer.chat_template is not None:
        return
    for name in ("chat_template.jinja", "chat_template.txt", "chat_template.json"):
        template_path = os.path.join(model_path, name)
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                tokenizer.chat_template = f.read()
            return


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


class MLPProjection(nn.Module):
    def __init__(self, input_dim=768, hidden_dim=None, output_dim=3584):
        super().__init__()
        if hidden_dim is None:
            hidden_dim = output_dim
        self.mlp = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim)
        )

    def forward(self, x):
        return self.mlp(x)


def load_models(codebook_path, mlp_path, llm_path, device):
    pq_codebook_model = PQCodebookModel(codebook_path, device=device)
    pq_codebook_model.eval()

    mlp_checkpoint = torch.load(mlp_path, map_location=device)
    mlp_model = MLPProjection(
        input_dim=mlp_checkpoint["input_dim"],
        hidden_dim=mlp_checkpoint["hidden_dim"],
        output_dim=mlp_checkpoint["output_dim"]
    )
    mlp_model.load_state_dict(mlp_checkpoint["mlp"])
    mlp_model.to(device)
    mlp_model.eval()

    tokenizer = AutoTokenizer.from_pretrained(llm_path, trust_remote_code=True)
    ensure_chat_template(tokenizer, llm_path)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    llm_dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    llm_model = AutoModelForCausalLM.from_pretrained(
        llm_path,
        trust_remote_code=True,
        torch_dtype=llm_dtype,
        device_map={"": str(device)}
    )
    llm_model.eval()

    placeholder_token = "<USR_EMB>"
    added = False
    if placeholder_token not in tokenizer.get_vocab():
        tokenizer.add_tokens([placeholder_token])
        added = True
    if added:
        llm_model.resize_token_embeddings(len(tokenizer))

    return pq_codebook_model, mlp_model, llm_model, tokenizer


def sample_random_embeddings(pq_codebook_model, batch_size, his_len, device, seed=None):
    if seed is not None:
        g = torch.Generator(device=device)
        g.manual_seed(seed)
    else:
        g = None
    num_subspaces = pq_codebook_model.num_subspaces
    codebook_size = pq_codebook_model.codebook_size
    subspace_dim = pq_codebook_model.subspace_dim

    indices = torch.randint(0, codebook_size, (batch_size, his_len, num_subspaces), generator=g, device=device)
    parts = []
    for i, codebook in enumerate(pq_codebook_model.codebooks):
        cb = codebook.to(device)
        flat_idx = indices[:, :, i].reshape(-1)
        picked = cb[flat_idx].reshape(batch_size, his_len, subspace_dim)
        parts.append(picked)
    embeddings = torch.cat(parts, dim=-1)
    return embeddings, indices


def embeddings_from_indices(pq_codebook_model, indices):
    batch_size, his_len, num_subspaces = indices.shape
    subspace_dim = pq_codebook_model.subspace_dim
    parts = []
    for i, codebook in enumerate(pq_codebook_model.codebooks):
        cb = codebook.to(indices.device)
        flat_idx = indices[:, :, i].reshape(-1)
        picked = cb[flat_idx].reshape(batch_size, his_len, subspace_dim)
        parts.append(picked)
    return torch.cat(parts, dim=-1)


def build_prompt(tokenizer, question, his_len, mode):
    placeholder_token = "<USR_EMB>"
    placeholder_str = " ".join([placeholder_token] * his_len)
    if mode == "demographic":
        instruction = (
            "You are given a user represented by the following embeddings. "
            "Describe the user's demographic profile (e.g., interests, occupation, communication style, values).\n\n"
            f"The user is represented by these embeddings: {placeholder_str}\n\n"
            "Demographic profile:"
        )
        messages = [{"role": "user", "content": instruction}]
    else:
        user_prompt_text = (
            "You are the USER described by the following embeddings.\n"
            f"The user is represented by these embeddings: {placeholder_str}\n"
            f"Answer the question as this user would answer: {question}\n"
            "Respond in the user's voice and preferences."
        )
        messages = [{"role": "user", "content": user_prompt_text}]
    formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return tokenizer(formatted, return_tensors="pt", padding=False, truncation=True, max_length=500)


def generate_batch(pq_codebook_model, mlp_model, llm_model, tokenizer,
                   question, his_len, max_new_tokens, device, batch_size,
                   do_sample, temperature, top_p, seed, mode, indices_override=None):
    if indices_override is not None:
        indices = indices_override.to(device)
        if indices.size(0) == 1 and batch_size > 1:
            indices = indices.repeat(batch_size, 1, 1)
        user_embeddings = embeddings_from_indices(pq_codebook_model, indices)
    else:
        user_embeddings, indices = sample_random_embeddings(
            pq_codebook_model, batch_size=batch_size, his_len=his_len, device=device, seed=seed
        )

    with torch.no_grad():
        llm_embs = mlp_model(user_embeddings)

    llm_embeddings = llm_model.get_input_embeddings()
    model_dtype = next(llm_model.parameters()).dtype
    llm_embs = llm_embs.to(dtype=model_dtype)

    input_ids_list = []
    placeholder_positions_list = []
    placeholder_id = tokenizer.convert_tokens_to_ids("<USR_EMB>")

    for _ in range(batch_size):
        inputs = build_prompt(tokenizer, question, his_len, mode)
        input_ids = inputs["input_ids"].squeeze(0)
        input_ids_list.append(input_ids)
        placeholder_positions = (input_ids == placeholder_id).nonzero(as_tuple=True)[0].tolist()
        placeholder_positions_list.append(placeholder_positions)

    max_seq_len = max(ids.size(0) for ids in input_ids_list)
    padded_input_ids = []
    attention_masks = []

    for input_ids in input_ids_list:
        seq_len = input_ids.size(0)
        pad_len = max_seq_len - seq_len
        pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        padded_input_ids.append(torch.cat([torch.full((pad_len,), pad_id, dtype=input_ids.dtype), input_ids]))
        attention_masks.append(torch.cat([torch.zeros(pad_len, dtype=torch.long), torch.ones(seq_len, dtype=torch.long)]))

    input_ids_batch = torch.stack(padded_input_ids).to(device)
    attention_mask_batch = torch.stack(attention_masks).to(device)

    input_embs = llm_embeddings(input_ids_batch).to(dtype=model_dtype)

    for i in range(batch_size):
        placeholder_positions = placeholder_positions_list[i]
        if len(placeholder_positions) >= his_len:
            pad_len = max_seq_len - input_ids_list[i].size(0)
            adjusted_positions = [pos + pad_len for pos in placeholder_positions[:his_len]]
            for j, pos in enumerate(adjusted_positions):
                if pos < max_seq_len:
                    input_embs[i, pos] = llm_embs[i, j]

    with torch.no_grad():
        outputs = llm_model.generate(
            inputs_embeds=input_embs,
            attention_mask=attention_mask_batch,
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
            temperature=temperature if do_sample else 1.0,
            top_p=top_p if do_sample else 1.0,
            pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id
        )

    generated_texts = []
    for i in range(batch_size):
        generated_ids = outputs[i]
        text = tokenizer.decode(generated_ids, skip_special_tokens=True)
        gen_len = generated_ids.size(0) - input_ids_list[i].size(0)
        if gen_len >= max_new_tokens:
            if not text.endswith("..."):
                text = text + "..."
        generated_texts.append(text)

    return generated_texts, indices


def generate_pair(pq_codebook_model, mlp_model_answer, mlp_model_demo, llm_model, tokenizer,
                  question, his_len, max_new_tokens, device, batch_size,
                  do_sample, temperature, top_p, seed, indices_override=None):
    # use same indices for demographic + answer
    if indices_override is not None:
        indices = indices_override.to(device)
        if indices.size(0) == 1 and batch_size > 1:
            indices = indices.repeat(batch_size, 1, 1)
        user_embeddings = embeddings_from_indices(pq_codebook_model, indices)
    else:
        user_embeddings, indices = sample_random_embeddings(
            pq_codebook_model, batch_size=batch_size, his_len=his_len, device=device, seed=seed
        )

    with torch.no_grad():
        llm_embs_answer = mlp_model_answer(user_embeddings)
        llm_embs_demo = mlp_model_demo(user_embeddings)

    def _generate_from_embs(llm_embs, mode):
        input_ids_list = []
        placeholder_positions_list = []
        placeholder_id = tokenizer.convert_tokens_to_ids("<USR_EMB>")
        for _ in range(batch_size):
            inputs = build_prompt(tokenizer, question, his_len, mode)
            input_ids = inputs["input_ids"].squeeze(0)
            input_ids_list.append(input_ids)
            placeholder_positions = (input_ids == placeholder_id).nonzero(as_tuple=True)[0].tolist()
            placeholder_positions_list.append(placeholder_positions)

        max_seq_len = max(ids.size(0) for ids in input_ids_list)
        padded_input_ids = []
        attention_masks = []
        for input_ids in input_ids_list:
            seq_len = input_ids.size(0)
            pad_len = max_seq_len - seq_len
            pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
            padded_input_ids.append(torch.cat([torch.full((pad_len,), pad_id, dtype=input_ids.dtype), input_ids]))
            attention_masks.append(torch.cat([torch.zeros(pad_len, dtype=torch.long), torch.ones(seq_len, dtype=torch.long)]))

        input_ids_batch = torch.stack(padded_input_ids).to(device)
        attention_mask_batch = torch.stack(attention_masks).to(device)

        llm_embeddings = llm_model.get_input_embeddings()
        model_dtype = next(llm_model.parameters()).dtype
        input_embs = llm_embeddings(input_ids_batch).to(dtype=model_dtype)
        llm_embs = llm_embs.to(dtype=model_dtype)

        for i in range(batch_size):
            placeholder_positions = placeholder_positions_list[i]
            if len(placeholder_positions) >= his_len:
                pad_len = max_seq_len - input_ids_list[i].size(0)
                adjusted_positions = [pos + pad_len for pos in placeholder_positions[:his_len]]
                for j, pos in enumerate(adjusted_positions):
                    if pos < max_seq_len:
                        input_embs[i, pos] = llm_embs[i, j]

        with torch.no_grad():
            outputs = llm_model.generate(
                inputs_embeds=input_embs,
                attention_mask=attention_mask_batch,
                max_new_tokens=max_new_tokens,
                do_sample=do_sample,
                temperature=temperature if do_sample else 1.0,
                top_p=top_p if do_sample else 1.0,
                pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id
            )

        generated_texts = []
        for i in range(batch_size):
            generated_ids = outputs[i]
            text = tokenizer.decode(generated_ids, skip_special_tokens=True)
            gen_len = generated_ids.size(0) - input_ids_list[i].size(0)
            if gen_len >= max_new_tokens:
                if not text.endswith("..."):
                    text = text + "..."
            generated_texts.append(text)
        return generated_texts

    answers = _generate_from_embs(llm_embs_answer, mode="answer")
    demos = _generate_from_embs(llm_embs_demo, mode="demographic")
    return answers, demos, indices


def create_app(args):
    app = Flask(__name__)

    device = torch.device(args.device)
    pq_codebook_model, mlp_model_answer, llm_model, tokenizer = load_models(
        args.codebook_path, args.mlp_path_answer, args.llm_path, device=device
    )
    _, mlp_model_demo, _, _ = load_models(
        args.codebook_path, args.mlp_path_demographic, args.llm_path, device=device
    )
    codebook_size = pq_codebook_model.codebook_size
    num_subspaces = pq_codebook_model.num_subspaces
    sphere_state = None
    pq_codebook_model_sphere = None
    if getattr(args, "sphere_map_path", None):
        try:
            loaded = torch.load(args.sphere_map_path, map_location="cpu")
            if "mean" in loaded and "W" in loaded:
                sphere_state = {"mean": loaded["mean"], "W": loaded["W"]}
                pq_codebook_model_sphere = PQCodebookModel(args.codebook_path, device="cpu")
        except Exception:
            sphere_state = None
            pq_codebook_model_sphere = None

    def compute_sphere_points(indices_tensor):
        if sphere_state is None or indices_tensor is None or pq_codebook_model_sphere is None:
            return []
        with torch.no_grad():
            indices_cpu = indices_tensor.detach().cpu()
            user_vecs = embeddings_from_indices(pq_codebook_model_sphere, indices_cpu).mean(dim=1)
            pts = project_to_uniform_sphere(user_vecs, sphere_state)
        return pts.cpu().tolist()

    sphere_background = []
    if sphere_state is not None:
        try:
            bg_n = 2000
            bg_indices = torch.randint(0, codebook_size, (bg_n, 8, num_subspaces))
            sphere_background = compute_sphere_points(bg_indices)
        except Exception:
            sphere_background = []

    def random_indices_values(his_len):
        rows = []
        for _ in range(his_len):
            row = torch.randint(0, codebook_size, (num_subspaces,)).tolist()
            rows.append(row)
        return rows

    def parse_custom_indices(form, his_len):
        use_custom = form.get("mode") == "custom"
        if not use_custom:
            return None, None

        indices_json = form.get("indices_json", "").strip()
        values = []
        for h in range(his_len):
            row = []
            for s in range(num_subspaces):
                key = f"idx_{h}_{s}"
                try:
                    v = int(form.get(key, 0))
                except Exception:
                    v = 0
                row.append(max(0, min(codebook_size - 1, v)))
            values.append(row)

        if indices_json:
            try:
                parsed = json.loads(indices_json)
                if isinstance(parsed, list):
                    values = parsed
            except Exception:
                pass

        if len(values) == his_len and all(isinstance(r, list) and len(r) == num_subspaces for r in values):
            tensor = torch.tensor(values, dtype=torch.long).unsqueeze(0)
            return tensor, values

        if isinstance(values, list) and len(values) == his_len * num_subspaces:
            rows = []
            for h in range(his_len):
                row = []
                for s in range(num_subspaces):
                    row.append(int(values[h * num_subspaces + s]))
                rows.append(row)
            tensor = torch.tensor(rows, dtype=torch.long).unsqueeze(0)
            return tensor, rows

        return None, None

    @app.route("/", methods=["GET"])
    def index():
        return render_template("welcome.html")

    @app.route("/demo", methods=["GET"])
    def demo():
        return render_template(
            "index.html",
            results=None,
            question="Between Trump and Harris, who do you support more, and why?",
            num_users=20,
            mode="answer",
            num_subspaces=num_subspaces,
            codebook_size=codebook_size,
            indices_values=random_indices_values(8),
            indices_json="",
            results_json_answer="",
            results_json_demographic="",
            results_json_both="",
            results_json_custom="",
            sphere_background=sphere_background,
            sphere_highlight=[],
            sphere_custom=[],
            sphere_enabled=sphere_state is not None
        )

    @app.route("/generate", methods=["POST"])
    def generate():
        question = request.form.get("question", "").strip()
        num_users = int(request.form.get("num_users", 20))
        his_len = 8
        max_new_tokens = 120
        mode = request.form.get("mode", "answer")
        if mode == "custom":
            num_users = 1

        do_sample = False if mode == "custom" else True
        temperature = 1.0
        top_p = 1.0

        indices_override, indices_values = parse_custom_indices(request.form, his_len)
        if mode == "custom" and indices_override is None:
            prev_custom = request.form.get("results_json_custom", "").strip()
            if prev_custom:
                try:
                    parsed = json.loads(prev_custom)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        last = parsed[-1]
                        last_indices = last.get("indices")
                        if isinstance(last_indices, list) and len(last_indices) == his_len:
                            tensor = torch.tensor(last_indices, dtype=torch.long).unsqueeze(0)
                            indices_override = tensor
                            indices_values = last_indices
                except Exception:
                    pass

        answers = []
        demos = []
        indices = None
        if mode == "both" or mode == "custom":
            answers, demos, indices = generate_pair(
                pq_codebook_model=pq_codebook_model,
                mlp_model_answer=mlp_model_answer,
                mlp_model_demo=mlp_model_demo,
                llm_model=llm_model,
                tokenizer=tokenizer,
                question=question,
                his_len=his_len,
                max_new_tokens=max_new_tokens,
                device=device,
                batch_size=num_users,
                do_sample=do_sample,
                temperature=temperature,
                top_p=top_p,
                seed=None,
                indices_override=indices_override
            )
        elif mode == "demographic":
            demos, indices = generate_batch(
                pq_codebook_model=pq_codebook_model,
                mlp_model=mlp_model_demo,
                llm_model=llm_model,
                tokenizer=tokenizer,
                question="",
                his_len=his_len,
                max_new_tokens=max_new_tokens,
                device=device,
                batch_size=num_users,
                do_sample=do_sample,
                temperature=temperature,
                top_p=top_p,
                seed=None,
                mode="demographic",
                indices_override=indices_override
            )
        else:
            answers, indices = generate_batch(
                pq_codebook_model=pq_codebook_model,
                mlp_model=mlp_model_answer,
                llm_model=llm_model,
                tokenizer=tokenizer,
                question=question,
                his_len=his_len,
                max_new_tokens=max_new_tokens,
                device=device,
                batch_size=num_users,
                do_sample=do_sample,
                temperature=temperature,
                top_p=top_p,
                seed=None,
                mode="answer",
                indices_override=indices_override
            )

        results = []
        prev_results_by_mode = {
            "answer": request.form.get("results_json_answer", "").strip(),
            "demographic": request.form.get("results_json_demographic", "").strip(),
            "both": request.form.get("results_json_both", "").strip(),
            "custom": request.form.get("results_json_custom", "").strip()
        }
        prev_results_json = prev_results_by_mode.get(mode, "")
        if mode == "custom" and prev_results_json:
            try:
                prev = json.loads(prev_results_json)
                if isinstance(prev, list):
                    results.extend(prev)
            except Exception:
                pass
        count = len(answers) if len(answers) > 0 else len(demos)
        for i in range(count):
            results.append({
                "index": len(results),
                "answer": answers[i] if len(answers) > 0 else "",
                "demographic": demos[i] if len(demos) > 0 else "",
                "indices": indices[i].detach().cpu().tolist()
            })

        sphere_highlight = []
        sphere_custom = []
        if sphere_state is not None:
            if mode == "custom":
                try:
                    indices_all = torch.tensor([r["indices"] for r in results], dtype=torch.long)
                    sphere_custom = compute_sphere_points(indices_all)
                except Exception:
                    sphere_custom = []
            else:
                try:
                    sphere_highlight = compute_sphere_points(indices)
                except Exception:
                    sphere_highlight = []

        results_json_by_mode = dict(prev_results_by_mode)
        results_json_by_mode[mode] = json.dumps(results, ensure_ascii=False)

        return render_template(
            "index.html",
            results=results,
            question=question,
            num_users=num_users,
            mode=mode,
            num_subspaces=num_subspaces,
            codebook_size=codebook_size,
            indices_values=indices_values or [[0 for _ in range(num_subspaces)] for _ in range(8)],
            indices_json=request.form.get("indices_json", ""),
            results_json_answer=results_json_by_mode["answer"],
            results_json_demographic=results_json_by_mode["demographic"],
            results_json_both=results_json_by_mode["both"],
            results_json_custom=results_json_by_mode["custom"],
            sphere_background=sphere_background,
            sphere_highlight=sphere_highlight,
            sphere_custom=sphere_custom,
            sphere_enabled=sphere_state is not None
        )

    @app.route("/reset", methods=["POST"])
    def reset():
        return redirect(url_for("demo"))

    return app


def parse_args():
    parser = argparse.ArgumentParser(description="Flask demo: random codebook combinations -> LLM responses")
    parser.add_argument("--codebook_path", type=str, required=True)
    parser.add_argument("--mlp_path_answer", type=str, required=True)
    parser.add_argument("--mlp_path_demographic", type=str, required=True)
    parser.add_argument("--llm_path", type=str, required=True)
    parser.add_argument("--sphere_map_path", type=str, default="")
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app = create_app(args)
    app.run(host=args.host, port=args.port, debug=False)

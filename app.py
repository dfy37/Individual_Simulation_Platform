from flask import Flask, render_template

app = Flask(__name__)

TEAM_MEMBERS = [
    {"name": "项目负责人", "role": "总体方案设计与研究统筹", "org": "Individual Simulation Platform"},
    {"name": "算法工程师", "role": "个体建模与行为生成", "org": "CURP / LifeSim"},
    {"name": "产品与前端", "role": "交互设计与可视化", "org": "Web Platform"},
]

PAPERS = [
    {"title": "Individual Representation Learning for Long-horizon Simulation", "venue": "ArXiv / Working Paper", "year": "2025"},
    {"title": "CURP: Controllable User Representation Parameterization", "venue": "Preprint", "year": "2025"},
    {"title": "LifeSim: User Life Event and Conversation Simulator", "venue": "Preprint", "year": "2025"},
]


@app.route('/')
def about():
    return render_template('about.html', active_page='about')


@app.route('/function')
def function_page():
    return render_template('function.html', active_page='function')


@app.route('/team')
def team():
    return render_template('team.html', active_page='team', members=TEAM_MEMBERS)


@app.route('/paper')
def paper():
    return render_template('paper.html', active_page='paper', papers=PAPERS)


@app.route('/contact')
def contact():
    return render_template('contact.html', active_page='contact')


@app.route('/curp')
def curp_entry():
    return render_template('curp_entry.html', active_page='function')


@app.route('/lifesim')
def lifesim_entry():
    return render_template('lifesim_entry.html', active_page='function')


@app.route('/coming-soon/<feature_key>')
def coming_soon(feature_key: str):
    feature_map = {
        'interview-modeling': '以访谈的形式进行深度的个体建模',
        'marketing-test': '营销方法的用户测试',
    }
    return render_template(
        'coming_soon.html',
        active_page='function',
        feature_name=feature_map.get(feature_key, '功能建设中'),
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)

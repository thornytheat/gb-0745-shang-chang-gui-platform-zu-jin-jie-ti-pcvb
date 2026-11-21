from app.web import find_run
from flask import Blueprint, jsonify

routes = Blueprint("joint", __name__)


@routes.get("/api/joint/<run_id>")
def inspect_joint(run_id):
    run = find_run(run_id)
    return jsonify(
        {
            key: run[key]
            for key in ("联合阶梯", "联合汇总", "联合挂起")
        }
    )

import io


def test_joint_stage_api_and_workbench(client):
    run = client.post("/api/runs", json={"sample": "joint", "stage": "joint"}).json
    assert run["joint_hold"] is False
    payload = client.get(f"/api/joint/{run['id']}").json
    assert "联合阶梯" in payload and "联合汇总" in payload and "联合挂起" in payload
    page = client.get(f"/work/joint?run={run['id']}")
    assert page.status_code == 200
    assert "联合组 G1 同商户联柜".encode() in page.data


def test_joint_run_is_idempotent(client):
    first = client.post("/api/runs", json={"sample": "joint"})
    second = client.post("/api/runs", json={"sample": "joint"})
    assert [first.status_code, second.status_code] == [201, 200]
    assert first.json["id"] == second.json["id"]
    assert len(client.get("/api/runs").json) == 1


def test_held_group_flag_returned_and_publish_banner(client):
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).resolve().parents[1] / "sample_data" / "joint.json").read_bytes())
    raw["groups"][0]["members"].append("999")
    response = client.post(
        "/api/runs",
        data={"period": "2026-11", "file": (io.BytesIO(json.dumps(raw).encode()), "账簿.json")},
    )
    assert response.status_code == 201
    assert response.json["joint_hold"] is True
    run_id = response.json["id"]
    publish = client.get(f"/work/publish?run={run_id}")
    assert "联合结算挂起".encode() in publish.data
    api = client.get(f"/api/publish/{run_id}").json
    assert api["联合挂起"] is True


def test_malformed_group_does_not_persist(client):
    before = len(client.get("/api/runs").json)
    response = client.post(
        "/api/runs",
        data={"file": (io.BytesIO(b'{"contracts":[],"holidays":[],"events":[],"statement":[],"groups":{}}'), "bad.json")},
    )
    assert response.status_code == 422
    assert len(client.get("/api/runs").json) == before


def test_legacy_samples_still_carry_joint_keys(client):
    for sample in ("mall", "clean"):
        run = client.post("/api/runs", json={"sample": sample}).json
        detail = client.get(f"/api/joint/{run['id']}")
        assert detail.status_code == 200
        assert detail.json["联合挂起"] is False

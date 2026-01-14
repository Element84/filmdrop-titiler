def test_health(app):
    response = app.get("/healthz")
    assert response.status_code == 200
    assert response.json == {"staus": "ok"}

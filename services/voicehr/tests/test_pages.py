def test_root_redirects_to_admin(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin"


def test_admin_page_is_available(client):
    from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME

    response = client.get("/admin", auth=(TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD))
    assert response.status_code == 200
    assert "VoiceHR Admin" in response.text
    assert "Completed Reports" in response.text
    assert "Retry Evaluation" in response.text


def test_candidate_page_is_available(client):
    response = client.get("/interview?token=test-token")
    assert response.status_code == 200
    assert "VoiceHR Interview" in response.text


def test_report_page_is_available(client):
    response = client.get("/report?token=test-token")
    assert response.status_code == 200
    assert "VoiceHR Report" in response.text

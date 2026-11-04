import os
from app.config import update_env_file, CleanupSettings


def test_update_env_file(tmp_path):
    env_file = os.path.join(tmp_path, ".env")

    # Initial write
    updates_1 = {
        "JUMPSERVER_HOST": "jump.exemplo.com.br",
        "JUMPSERVER_PORT": "2222",
        "JUMPSERVER_USER": "admin.teste",
    }
    update_env_file(updates_1, env_file)

    assert os.path.exists(env_file)
    with open(env_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "JUMPSERVER_HOST=jump.exemplo.com.br" in content
    assert "JUMPSERVER_USER=admin.teste" in content

    # Second write (update existing and add new)
    updates_2 = {
        "JUMPSERVER_USER": "novo.usuario",
        "JUMPSERVER_ACCOUNT": "auth.gpon",
    }
    update_env_file(updates_2, env_file)

    with open(env_file, "r", encoding="utf-8") as f:
        new_content = f.read()

    assert "JUMPSERVER_USER=novo.usuario" in new_content
    assert "JUMPSERVER_ACCOUNT=auth.gpon" in new_content
    assert "JUMPSERVER_HOST=jump.exemplo.com.br" in new_content  # preserved


import json
import os
from pathlib import Path
import pytest
import re
import socket
import subprocess
import time
import unittest.mock as mock

from .util import docker_run, random_string


@pytest.fixture(scope="session")
def httpserver_listen_address():
    if os.getenv("GITHUB_ACTIONS") == "true":
        # we can't use host.docker.internal, because it doesn't work on GitHub actions
        return (LOCAL_IP_ADDRESS, None)
    else:
        # but, using the host's local IP doesn't work locally, so use defaults there
        return (None, None)


def test_queue_worker_files(
    docker_image, docker_network, redis_client, upload_server, httpserver
):
    project_dir = Path(__file__).parent / "fixtures/file-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with open(upload_server / "input.txt", "w") as f:
        f.write("test")

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "http://upload-server:5000/upload",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        # we expect a webhook on starting
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        final_response = None

        def capture_final_response(request):
            nonlocal final_response
            final_response = request.get_json()

        # and another on finishing
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": "http://upload-server:5000/download/output.txt",
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        ).respond_with_handler(capture_final_response)

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )
        predict_id = random_string(10)

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "text": "baz",
                                "path": "http://upload-server:5000/download/input.txt",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        assert re.match(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{6}",
            final_response["x-experimental-timestamps"]["started_at"],
        )
        assert re.match(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{6}",
            final_response["x-experimental-timestamps"]["completed_at"],
        )

        with open(upload_server / "output.txt") as f:
            assert f.read() == "foobaztest"


def test_queue_worker_yielding_file(
    docker_network, docker_image, redis_client, upload_server, httpserver
):
    project_dir = Path(__file__).parent / "fixtures/yielding-file-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with open(upload_server / "input.txt", "w") as f:
        f.write("test")

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "http://upload-server:5000/upload",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["http://upload-server:5000/download/out-0.txt"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": [
                    "http://upload-server:5000/download/out-0.txt",
                    "http://upload-server:5000/download/out-1.txt",
                ],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": [
                    "http://upload-server:5000/download/out-0.txt",
                    "http://upload-server:5000/download/out-1.txt",
                    "http://upload-server:5000/download/out-2.txt",
                ],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": [
                    "http://upload-server:5000/download/out-0.txt",
                    "http://upload-server:5000/download/out-1.txt",
                    "http://upload-server:5000/download/out-2.txt",
                ],
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "path": "http://upload-server:5000/download/input.txt",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        with open(upload_server / "out-0.txt") as f:
            assert f.read() == "test foo"
        with open(upload_server / "out-1.txt") as f:
            assert f.read() == "test bar"
        with open(upload_server / "out-2.txt") as f:
            assert f.read() == "test baz"


def test_queue_worker_yielding(docker_network, docker_image, redis_client, httpserver):
    project_dir = Path(__file__).parent / "fixtures/yielding-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["foo", "bar", "baz"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["foo", "bar", "baz"],
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "text": "bar",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result


def test_queue_worker_error(docker_network, docker_image, redis_client, httpserver):
    project_dir = Path(__file__).parent / "fixtures/failing-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        # There's a timing issue with this test. Locally, this request doesn't
        # make it, because the stack trace logs never come through. On GitHub
        # actions, the stack trace logs *do* come through. Set up a request
        # handler which can be, but does not have to be, called.
        httpserver.expect_request(
            "/webhook",
            json={
                "logs": mock.ANY,  # includes a stack trace
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        ).respond_with_data("OK")

        final_response = None

        def capture_final_response(request):
            nonlocal final_response
            final_response = request.get_json()

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "error": "over budget",
                "logs": mock.ANY,  # might include a stack trace (see above)
                "output": None,
                "status": "failed",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        ).respond_with_handler(capture_final_response)

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "text": "bar",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result


def test_queue_worker_error_after_output(
    docker_network, docker_image, redis_client, httpserver
):
    project_dir = Path(__file__).parent / "fixtures/failing-after-output-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["hello bar"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": ["a printed log message"],
                "output": ["hello bar"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        # There's a timing issue with this test. Sometimes (rarely?) on GitHub
        # actions, the stack trace logs don't make it. Set up a request handler
        # which can be, but does not have to be, called.
        httpserver.expect_request(
            "/webhook",
            json={
                "logs": mock.ANY,  # includes a stack trace
                "output": ["hello bar"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        ).respond_with_data("OK")

        final_response = None

        def capture_final_response(request):
            nonlocal final_response
            final_response = request.get_json()

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "error": "mid run error",
                "logs": mock.ANY,  # might include a stack trace
                "output": ["hello bar"],
                "status": "failed",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        ).respond_with_handler(capture_final_response)

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "text": "bar",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        # TODO Debug timing issue so we can reliably assert that tracebacks get logged
        # assert "Traceback (most recent call last):" in final_response["logs"]


def test_queue_worker_invalid_input(
    docker_network, docker_image, redis_client, httpserver
):
    project_dir = Path(__file__).parent / "fixtures/int-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        final_response = None

        def capture_final_response(request):
            nonlocal final_response
            final_response = request.get_json()

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "error": mock.ANY,
                "logs": [],
                "output": None,
                "status": "failed",
            },
            method="POST",
        ).respond_with_handler(capture_final_response)

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "num": "not a number",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        assert "value is not a valid integer" in final_response["error"]


def test_queue_worker_logging(docker_network, docker_image, redis_client, httpserver):
    project_dir = Path(__file__).parent / "fixtures/logging-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [
                    "WARNING:root:writing log message",
                ],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [
                    "WARNING:root:writing log message",
                    "writing from C",
                ],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [
                    "WARNING:root:writing log message",
                    "writing from C",
                    "writing to stderr",
                ],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [
                    "WARNING:root:writing log message",
                    "writing from C",
                    "writing to stderr",
                    "writing with print",
                ],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [
                    "WARNING:root:writing log message",
                    "writing from C",
                    "writing to stderr",
                    "writing with print",
                ],
                "output": "output",
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {},
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result


def test_queue_worker_timeout(docker_network, docker_image, redis_client, httpserver):
    project_dir = Path(__file__).parent / "fixtures/timeout-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
            "2",  # timeout
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": "it worked after 0.1 seconds!",
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "sleep_time": 0.1,
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "error": "Prediction timed out",
                "logs": [],
                "output": None,
                "status": "failed",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        predict_id = random_string(10)

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "sleep_time": 3.0,
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": "it worked after 0.2 seconds!",
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        predict_id = random_string(10)

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "sleep_time": 0.2,
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result


def test_queue_worker_yielding_timeout(
    docker_image, docker_network, redis_client, httpserver
):
    project_dir = Path(__file__).parent / "fixtures/yielding-timeout-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
            "2",  # timeout
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["yield 0"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["yield 0"],
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "sleep_time": 0.1,
                                "n_iterations": 1,
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["yield 0"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": ["yield 0", "yield 1"],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "error": "Prediction timed out",
                "logs": [],
                "output": ["yield 0", "yield 1"],
                "status": "failed",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        predict_id = random_string(10)

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "sleep_time": 0.8,
                                "n_iterations": 10,
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result


def test_queue_worker_complex_output(
    docker_network, docker_image, redis_client, httpserver
):
    project_dir = Path(__file__).parent / "fixtures/complex-output-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": {
                    "hello": "hello world",
                    "goodbye": "goodbye world",
                },
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {
                                "name": "world",
                            },
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result


# Testing make_pickable works with sufficiently complex things.
# We're also testing uploading files because that is a separate code path in the make redis worker.
# Shame this is an integration test but want to make sure this works for erlich without loads of manual testing.
# Maybe this can be removed when we have better unit test coverage for redis things.
def test_queue_worker_yielding_list_of_complex_output(
    docker_network, docker_image, redis_client, upload_server, httpserver
):
    project_dir = (
        Path(__file__).parent / "fixtures/yielding-list-of-complex-output-project"
    )
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "http://upload-server:5000/upload",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": None,
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": [
                    [
                        {
                            "file": "http://upload-server:5000/download/file",
                            "text": "hello",
                        }
                    ]
                ],
                "status": "processing",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                },
            },
            method="POST",
        )

        httpserver.expect_oneshot_request(
            "/webhook",
            json={
                "logs": [],
                "output": [
                    [
                        {
                            "file": "http://upload-server:5000/download/file",
                            "text": "hello",
                        }
                    ]
                ],
                "status": "succeeded",
                "x-experimental-timestamps": {
                    "started_at": mock.ANY,
                    "completed_at": mock.ANY,
                },
            },
            method="POST",
        )

        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )

        with httpserver.wait(timeout=15) as waiting:
            redis_client.xadd(
                name="predict-queue",
                fields={
                    "value": json.dumps(
                        {
                            "id": predict_id,
                            "input": {},
                            "webhook": webhook_url,
                        }
                    ),
                },
            )

        # check we received all the webhooks
        assert waiting.result

        with open(upload_server / "file") as f:
            assert f.read() == "hello"


# the worker shouldn't start taking jobs until the runner has finished setup
def test_queue_worker_setup(docker_network, docker_image, redis_client, httpserver):
    project_dir = Path(__file__).parent / "fixtures/long-setup-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        httpserver.expect_request("/webhook", method="POST")
        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        webhook_url = httpserver.url_for("/webhook").replace(
            "localhost", "host.docker.internal"
        )
        redis_client.xadd(
            name="predict-queue",
            fields={
                "value": json.dumps(
                    {
                        "id": predict_id,
                        "input": {},
                        "webhook": webhook_url,
                    }
                ),
            },
        )

        predict_id = random_string(10)
        redis_client.xadd(
            name="predict-queue",
            fields={
                "value": json.dumps(
                    {
                        "id": predict_id,
                        "input": {},
                        "webhook": webhook_url,
                    }
                ),
            },
        )

        predict_id = random_string(10)
        redis_client.xadd(
            name="predict-queue",
            fields={
                "value": json.dumps(
                    {
                        "id": predict_id,
                        "input": {},
                        "webhook": webhook_url,
                    }
                ),
            },
        )

        # give it about five seconds to get properly into setup
        time.sleep(5)
        predictions_in_progress = redis_client.xpending(
            name="predict-queue", groupname="predict-queue"
        )["pending"]
        assert predictions_in_progress == 0

        # give it another 10s to finish setup
        time.sleep(10)
        predictions_in_progress = redis_client.xpending(
            name="predict-queue", groupname="predict-queue"
        )["pending"]
        assert predictions_in_progress == 1


def test_queue_worker_redis_responses(docker_network, docker_image, redis_client):
    project_dir = Path(__file__).parent / "fixtures/int-project"
    subprocess.run(["cog", "build", "-t", docker_image], check=True, cwd=project_dir)

    with docker_run(
        image=docker_image,
        interactive=True,
        network=docker_network,
        command=[
            "python",
            "-m",
            "cog.server.redis_queue",
            "redis",
            "6379",
            "predict-queue",
            "",
            "test-worker",
            "model_id",
            "logs",
        ],
    ):
        redis_client.xgroup_create(
            mkstream=True, groupname="predict-queue", name="predict-queue", id="$"
        )

        predict_id = random_string(10)
        redis_client.xadd(
            name="predict-queue",
            fields={
                "value": json.dumps(
                    {
                        "id": predict_id,
                        "input": {
                            "num": 42,
                        },
                        "response_queue": "response-queue",
                    }
                ),
            },
        )

        responses = response_iterator(redis_client, "response-queue")

        response = next(responses)
        assert response == {
            "logs": [],
            "output": None,
            "status": "processing",
            "x-experimental-timestamps": {
                "started_at": mock.ANY,
            },
        }

        response = next(responses)
        assert response == {
            "logs": [],
            "output": 84,
            "status": "succeeded",
            "x-experimental-timestamps": {
                "started_at": mock.ANY,
                "completed_at": mock.ANY,
            },
        }


def response_iterator(redis_client, response_queue, timeout=10):
    redis_client.config_set("notify-keyspace-events", "KEA")
    channel = redis_client.pubsub()
    channel.subscribe(f"__keyspace@0__:{response_queue}")

    while True:
        start = time.time()

        while time.time() - start < timeout:
            message = channel.get_message()
            if message and message["data"] == b"set":
                yield json.loads(redis_client.get(response_queue))
            time.sleep(0.01)

        raise TimeoutError("Timed out waiting for Redis message")


def get_local_ip_address():
    """
    Find our local IP address by opening a socket and checking where it
    connected from.
    """

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # we don't need a reachable destination!
    s.connect(("10.254.254.254", 1))
    ip_addr = s.getsockname()[0]
    s.close()

    return ip_addr


LOCAL_IP_ADDRESS = get_local_ip_address()

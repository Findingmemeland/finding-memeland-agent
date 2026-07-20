"""Fix 2 (post-mortem P0 candidate #2) — every tweepy client gets a hard HTTP
timeout. tweepy.Client exposes none and requests defaults to NONE: one hung
socket froze the Genesis hunt loop silently (no exception, no log)."""

from finding_memeland.social.x_client import _HTTP_TIMEOUT_S, XClient


def _timeout_of(client) -> float:
    # _with_timeout binds the timeout via functools.partial on session.request.
    return client.session.request.keywords["timeout"]


def test_main_v2_client_has_session_timeout():
    xc = XClient(
        api_key="k", api_secret="s",
        main_access_token="t", main_access_secret="ts",
    )
    assert _timeout_of(xc._client) == _HTTP_TIMEOUT_S


def test_persona_v2_clients_have_session_timeout():
    xc = XClient(api_key="k", api_secret="s")
    assert _timeout_of(xc._persona_v2("tok", "sec")) == _HTTP_TIMEOUT_S


def test_v11_api_has_timeout():
    xc = XClient(api_key="k", api_secret="s")
    assert xc._api_for("tok", "sec").timeout == _HTTP_TIMEOUT_S


def test_timeout_error_surfaces_as_loop_notification():
    """A timeout must raise OUT of poll() so the orchestrator's phase-1 handler
    notifies and retries — the whole point is loud failure instead of a hang."""
    from finding_memeland.orchestrator.simulation import build_simulation

    class _TimingOutOnce:
        def __init__(self, inner):
            self._inner = inner
            self._raised = False

        def poll(self, since):
            if not self._raised:
                self._raised = True
                raise TimeoutError("HTTPSConnectionPool: read timed out (30s)")
            return self._inner.poll(since)

    rig = build_simulation()
    rig.orchestrator._dm_source = _TimingOutOnce(rig.orchestrator._dm_source)
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state.value == "done"
    assert any("DM poll failed" in m and "timed out" in m for m in rig.notifier.messages)

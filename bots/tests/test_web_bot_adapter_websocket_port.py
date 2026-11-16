import socket
import threading
import time
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from bots.web_bot_adapter.web_bot_adapter import WebBotAdapter


class WebsocketPortSelectionTest(SimpleTestCase):
    def start_server(self, fixed_port):
        adapter = SimpleNamespace(
            websocket_port=None,
            websocket_server=None,
            livekit_websocket_bridge=None,
            handle_websocket=lambda websocket: None,
            get_websocket_port=lambda: fixed_port,
        )
        threading.Thread(target=WebBotAdapter.run_websocket_server, args=(adapter,), daemon=True).start()
        deadline = time.time() + 5
        while adapter.websocket_port is None and time.time() < deadline:
            time.sleep(0.01)
        self.addCleanup(lambda: adapter.websocket_server and adapter.websocket_server.shutdown())
        return adapter.websocket_port

    def free_port(self):
        with socket.socket() as s:
            s.bind(("localhost", 0))
            return s.getsockname()[1]

    @override_settings(ENABLE_BOT_WEBSOCKET_PORT_SELECTION_BY_OS=False)
    def test_uses_fixed_port_by_default(self):
        port = self.free_port()
        self.assertEqual(self.start_server(port), port)

    @override_settings(ENABLE_BOT_WEBSOCKET_PORT_SELECTION_BY_OS=True)
    def test_os_picks_distinct_ports_when_enabled(self):
        port = self.free_port()
        ports = {self.start_server(port) for _ in range(12)}
        self.assertNotIn(None, ports)
        self.assertEqual(len(ports), 12)


class TeamsContentSecurityPolicyBypassTest(SimpleTestCase):
    """Teams' CSP only lets the page reach ws://localhost:8097."""

    def bypasses(self, websocket_port):
        from bots.teams_bot_adapter.teams_bot_adapter import TeamsBotAdapter

        adapter = SimpleNamespace(websocket_port=websocket_port, get_websocket_port=lambda: 8097)
        return TeamsBotAdapter.subclass_specific_bypass_content_security_policy(adapter)

    def test_not_needed_on_the_allowed_port(self):
        self.assertFalse(self.bypasses(8097))

    def test_needed_on_any_other_port(self):
        self.assertTrue(self.bypasses(59083))
        self.assertTrue(self.bypasses(8098))

    def test_other_web_bots_keep_the_page_policy(self):
        self.assertFalse(WebBotAdapter.subclass_specific_bypass_content_security_policy(SimpleNamespace()))

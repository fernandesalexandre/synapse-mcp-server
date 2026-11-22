import unittest
import unittest.mock as mock
import json
import os
import sys

# Garante importação do app local
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import execute_enviar_mensagem

class TestRoutingContract(unittest.TestCase):
    def setUp(self):
        self.mock_urlopen_patcher = mock.patch("urllib.request.urlopen")
        self.mock_urlopen = self.mock_urlopen_patcher.start()
        self.last_sent_body = None

        def side_effect(req, *args, **kwargs):
            if hasattr(req, "data") and req.data:
                self.last_sent_body = json.loads(req.data.decode("utf-8"))
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = json.dumps([{"id": "test-uuid-mock"}]).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        self.mock_urlopen.side_effect = side_effect

    def tearDown(self):
        self.mock_urlopen_patcher.stop()

    def test_missing_destinatario_fails(self):
        """FAIL 1: Destinatário ausente deve ser rejeitado imediatamente."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Mensagem para Nikola"})
        self.assertIn("FAIL_ROTEAMENTO_SEM_DESTINATARIO", str(ctx.exception))

    def test_unknown_agent_fails(self):
        """Agente desconhecido fora do registry deve ser rejeitado."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Texto", "destinatario": "UNKNOWN_AGENT"})
        self.assertIn("Destinatario 'UNKNOWN_AGENT' invalido", str(ctx.exception))

    def test_cross_node_conflict_fails(self):
        """Destinatário privado com nó divergente deve ser rejeitado."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Texto", "destinatario": "ISAAC", "target_node_id": "PC2"})
        self.assertIn("CONFLITO_AUTORIDADE_DESTINO", str(ctx.exception))

    def test_private_recipient_with_all_node_fails(self):
        """FAIL residual: Destinatário privado com target_node_id='ALL' DEVE FALHAR."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Texto", "destinatario": "ISAAC", "target_node_id": "ALL"})
        self.assertIn("O no ALL e proibido para mensagens nominais privadas", str(ctx.exception))

    def test_private_recipient_resolves_deterministic_node_and_queue(self):
        """Destinatário privado ISAAC resolve estritamente PC1 e WAITING_ANTIGRAVITY."""
        res = execute_enviar_mensagem({"payload": "Mencao a Nikola e Atlas", "destinatario": "ISAAC"})
        self.assertEqual(res["status"], "ENVIADO")
        self.assertIsNotNone(self.last_sent_body)
        meta = self.last_sent_body["metadata"]
        self.assertEqual(meta["recipient_agent_id"], "ISAAC")
        self.assertEqual(meta["recipient_node_id"], "PC1")
        self.assertEqual(meta["target_node_id"], "PC1")
        self.assertEqual(self.last_sent_body["target_queue"], "WAITING_ANTIGRAVITY")

    def test_alias_codex_canonicalizes_to_atlas(self):
        """Alias CODEX é canonicalizado para ATLAS no nó PC1_CODEX."""
        res = execute_enviar_mensagem({"payload": "Diretriz", "destinatario": "CODEX"})
        self.assertEqual(res["status"], "ENVIADO")
        meta = self.last_sent_body["metadata"]
        self.assertEqual(meta["recipient_agent_id"], "ATLAS")
        self.assertEqual(meta["recipient_node_id"], "PC1_CODEX")
        self.assertEqual(meta["target_node_id"], "PC1_CODEX")
        self.assertEqual(self.last_sent_body["target_queue"], "WAITING_ANTIGRAVITY")

    def test_alita_routes_strictly_to_waiting_alita(self):
        """Destinatário ALITA resolve CLOUD_OPENAI e WAITING_ALITA."""
        res = execute_enviar_mensagem({"payload": "Resposta para Alita", "destinatario": "ALITA"})
        self.assertEqual(res["status"], "ENVIADO")
        meta = self.last_sent_body["metadata"]
        self.assertEqual(meta["recipient_agent_id"], "ALITA")
        self.assertEqual(meta["recipient_node_id"], "CLOUD_OPENAI")
        self.assertEqual(meta["target_node_id"], "CLOUD_OPENAI")
        self.assertEqual(self.last_sent_body["target_queue"], "WAITING_ALITA")

    def test_queue_conflict_rejection(self):
        """Conflito de fila com destinatário é rejeitado."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Texto", "destinatario": "ALITA", "target_queue": "WAITING_ANTIGRAVITY"})
        self.assertIn("CONFLITO_FILA_DESTINATARIO", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Texto", "destinatario": "ISAAC", "target_queue": "WAITING_ALITA"})
        self.assertIn("CONFLITO_FILA_DESTINATARIO", str(ctx.exception))

    def test_broadcast_requires_all_node(self):
        """Broadcast com nó individual deve ser rejeitado."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Broadcast", "destinatario": "ALL", "target_node_id": "PC1"})
        self.assertIn("Broadcast (destinatario='ALL') exige target_node_id='ALL'", str(ctx.exception))

    def test_broadcast_success(self):
        """Broadcast com ALL/ALL é aceito com sucesso na fila WAITING_ANTIGRAVITY."""
        res = execute_enviar_mensagem({"payload": "Aviso geral", "destinatario": "ALL", "target_node_id": "ALL"})
        self.assertEqual(res["status"], "ENVIADO")
        meta = self.last_sent_body["metadata"]
        self.assertEqual(meta["recipient_agent_id"], "ALL")
        self.assertEqual(meta["recipient_node_id"], "ALL")
        self.assertEqual(meta["target_node_id"], "ALL")
        self.assertEqual(self.last_sent_body["target_queue"], "WAITING_ANTIGRAVITY")

if __name__ == "__main__":
    unittest.main()

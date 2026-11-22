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
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps([{"id": "test-uuid-mock"}]).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        self.mock_urlopen.return_value = mock_resp

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
        """FAIL residual apontado por Atlas: Destinatário privado com target_node_id='ALL' DEVE FALHAR."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Texto", "destinatario": "ISAAC", "target_node_id": "ALL"})
        self.assertIn("O no ALL e proibido para mensagens nominais privadas", str(ctx.exception))

    def test_private_recipient_resolves_deterministic_node(self):
        """Destinatário privado com nó omitido resolve estritamente para seu nó registrado."""
        res = execute_enviar_mensagem({"payload": "Mencao a Nikola e Atlas", "destinatario": "ISAAC"})
        self.assertEqual(res["status"], "ENVIADO")

    def test_alias_codex_canonicalizes_to_atlas(self):
        """Alias CODEX é canonicalizado para ATLAS no nó PC1_CODEX."""
        res = execute_enviar_mensagem({"payload": "Diretriz", "destinatario": "CODEX"})
        self.assertEqual(res["status"], "ENVIADO")

    def test_broadcast_requires_all_node(self):
        """Broadcast com nó individual deve ser rejeitado."""
        with self.assertRaises(ValueError) as ctx:
            execute_enviar_mensagem({"payload": "Broadcast", "destinatario": "ALL", "target_node_id": "PC1"})
        self.assertIn("Broadcast (destinatario='ALL') exige target_node_id='ALL'", str(ctx.exception))

    def test_broadcast_success(self):
        """Broadcast com ALL/ALL é aceito com sucesso."""
        res = execute_enviar_mensagem({"payload": "Aviso geral", "destinatario": "ALL", "target_node_id": "ALL"})
        self.assertEqual(res["status"], "ENVIADO")

if __name__ == "__main__":
    unittest.main()

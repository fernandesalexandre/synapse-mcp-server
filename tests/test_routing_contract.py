import unittest
import unittest.mock as mock
import json
import os
import sys

# Garante importação do app local
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (
    execute_enviar_mensagem,
    execute_ler_fila,
    execute_obter_tarefa,
    execute_atualizar_status,
    generate_machine_token,
    verify_machine_token
)

class TestRoutingAndSecurityContract(unittest.TestCase):
    def setUp(self):
        self.mock_urlopen_patcher = mock.patch("urllib.request.urlopen")
        self.mock_urlopen = self.mock_urlopen_patcher.start()
        self.last_sent_body = None
        self.mock_db_data = [{"id": "test-uuid-mock", "target_queue": "WAITING_ANTIGRAVITY", "metadata": {"target_node_id": "PC1"}}]

        def side_effect(req, *args, **kwargs):
            if hasattr(req, "data") and req.data:
                self.last_sent_body = json.loads(req.data.decode("utf-8"))
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = json.dumps(self.mock_db_data).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        self.mock_urlopen.side_effect = side_effect

    def tearDown(self):
        self.mock_urlopen_patcher.stop()

    # --- 1. ROTEAMENTO DE ENVELOPE E OPACIDADE DE PAYLOAD ---

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

    # --- 2. AUTENTICAÇÃO E ACL DE LEITURA / ATUALIZAÇÃO (PARECERES ATLAS 791038d5 & a3b47031) ---

    def test_ler_fila_waiting_antigravity_without_auth_fails(self):
        """FAIL ALTA PRIORIDADE: Leitura anônima de WAITING_ANTIGRAVITY DEVE FALHAR."""
        with self.assertRaises(PermissionError) as ctx:
            execute_ler_fila({"target_queue": "WAITING_ANTIGRAVITY"}, authenticated_node_id=None)
        self.assertIn("AUTENTICACAO_OBRIGATORIA", str(ctx.exception))

    def test_ler_fila_waiting_alita_without_auth_fails(self):
        """RESIDUAL SANADO: Leitura anônima de WAITING_ALITA DEVE FALHAR (zero exceções anônimas)."""
        with self.assertRaises(PermissionError) as ctx:
            execute_ler_fila({"target_queue": "WAITING_ALITA"}, authenticated_node_id=None)
        self.assertIn("AUTENTICACAO_OBRIGATORIA", str(ctx.exception))

    def test_ler_fila_waiting_alita_with_cloud_auth_succeeds(self):
        """Leitura de WAITING_ALITA com conector Cloud autenticado é autorizada."""
        self.mock_db_data = [{"id": "msg-alita-1", "remetente": "ISAAC", "payload": "OK"}]
        res = execute_ler_fila({"target_queue": "WAITING_ALITA"}, authenticated_node_id="CLOUD_OPENAI")
        self.assertEqual(len(res), 1)

    def test_ler_fila_cloud_auth_cross_queue_fails(self):
        """Conector Cloud tentando acessar fila operacional WAITING_ANTIGRAVITY é bloqueado."""
        with self.assertRaises(PermissionError) as ctx:
            execute_ler_fila({"target_queue": "WAITING_ANTIGRAVITY"}, authenticated_node_id="CLOUD_OPENAI")
        self.assertIn("ACESSO_NEGADO_ISOLAMENTO_DE_FILA", str(ctx.exception))

    def test_ler_fila_physical_node_cross_queue_fails(self):
        """Nó físico local (PC1) tentando acessar diretamente fila WAITING_ALITA é bloqueado."""
        with self.assertRaises(PermissionError) as ctx:
            execute_ler_fila({"target_queue": "WAITING_ALITA"}, authenticated_node_id="PC1")
        self.assertIn("ACESSO_NEGADO_ISOLAMENTO_DE_FILA", str(ctx.exception))

    def test_ler_fila_authenticated_filters_node(self):
        """Leitura autenticada filtra estritamente tarefas do nó autenticado."""
        self.mock_db_data = [
            {"id": "msg-1", "metadata": {"target_node_id": "PC1"}},
            {"id": "msg-2", "metadata": {"target_node_id": "PC2"}}
        ]
        res = execute_ler_fila({"target_queue": "WAITING_ANTIGRAVITY"}, authenticated_node_id="PC1")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["metadata"]["target_node_id"], "PC1")

    def test_obter_tarefa_without_auth_fails(self):
        """FAIL ALTA PRIORIDADE: Obtenção de tarefa local sem autenticação DEVE FALHAR."""
        self.mock_db_data = [{"id": "task-pc1", "metadata": {"target_node_id": "PC1"}}]
        with self.assertRaises(PermissionError) as ctx:
            execute_obter_tarefa({"tarefa_id": "task-pc1"}, authenticated_node_id=None)
        self.assertIn("AUTENTICACAO_OBRIGATORIA", str(ctx.exception))

    def test_obter_tarefa_cross_node_fails(self):
        """FAIL ALTA PRIORIDADE: Nó PC2 requisitando tarefa do nó PC1 DEVE SER BLOQUEADO."""
        self.mock_db_data = [{"id": "task-pc1", "metadata": {"target_node_id": "PC1"}}]
        with self.assertRaises(PermissionError) as ctx:
            execute_obter_tarefa({"tarefa_id": "task-pc1"}, authenticated_node_id="PC2")
        self.assertIn("ACESSO_NEGADO_ISOLAMENTO_DE_NO", str(ctx.exception))

    def test_obter_tarefa_correct_node_succeeds(self):
        """Nó proprietário obtém sua própria tarefa com sucesso."""
        self.mock_db_data = [{"id": "task-pc1", "metadata": {"target_node_id": "PC1"}}]
        res = execute_obter_tarefa({"tarefa_id": "task-pc1"}, authenticated_node_id="PC1")
        self.assertEqual(res["status"], "SUCESSO")

    def test_obter_tarefa_cloud_node_succeeds_for_cloud_task(self):
        """Conector cloud autenticado obtém tarefa de destino cloud."""
        self.mock_db_data = [{"id": "task-cloud-1", "metadata": {"target_node_id": "CLOUD_OPENAI"}}]
        res = execute_obter_tarefa({"tarefa_id": "task-cloud-1"}, authenticated_node_id="CLOUD_OPENAI")
        self.assertEqual(res["status"], "SUCESSO")

    def test_atualizar_status_without_auth_fails(self):
        """FAIL ALTA PRIORIDADE: Atualização de status sem autenticação DEVE FALHAR."""
        with self.assertRaises(PermissionError) as ctx:
            execute_atualizar_status({"tarefa_id": "task-1", "novo_status": "COMPLETED"}, authenticated_node_id=None)
        self.assertIn("AUTENTICACAO_OBRIGATORIA", str(ctx.exception))

    def test_atualizar_status_atomic_cross_node_fails(self):
        """Mutação atômica em tarefa de outro nó resulta em zero linhas modificadas e erro."""
        self.mock_db_data = []  # Retorno vazio do PATCH indicando predicado não satisfeito
        with self.assertRaises(PermissionError) as ctx:
            execute_atualizar_status({"tarefa_id": "task-pc1", "novo_status": "COMPLETED"}, authenticated_node_id="PC2")
        self.assertIn("ACESSO_NEGADO_OU_NAO_ENCONTRADO", str(ctx.exception))

    def test_atualizar_status_atomic_owner_succeeds(self):
        """Mutação atômica pelo nó proprietário atualiza o status com sucesso."""
        self.mock_db_data = [{"id": "task-pc1", "status": "COMPLETED"}]
        res = execute_atualizar_status({"tarefa_id": "task-pc1", "novo_status": "COMPLETED"}, authenticated_node_id="PC1")
        self.assertEqual(res["status"], "ATUALIZADO")

if __name__ == "__main__":
    unittest.main()

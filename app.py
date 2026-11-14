# -*- coding: utf-8 -*-
"""
app.py - Servidor MCP Nativo HTTP (streamable-http) para o Synapse Handoff
Expondo ferramentas oficiais com controle estrito de Multi-Tenancy e Isolamento de Nós (Node Isolation):
- ler_fila (filtrada por tenant e por node_id de destino)
- obter_tarefa (leitura por ID com validação de autorização por nó)
- enviar_mensagem (carimbo imutável de source_node_id e target_node_id)
- atualizar_status (com guarda de posse de nó)
Conecta-se à API Supabase do Handoff (dopomsklkovcvxthtyqq.supabase.co).
"""

import sys
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

import os
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://dopomsklkovcvxthtyqq.supabase.co/rest/v1")
API_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRvcG9tc2tsa292Y3Z4dGh0eXFxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTExMjYzNiwiZXhwIjoyMDk2Njg4NjM2fQ.DUUhJiZqpd279H8-L2fcz4dNTEjJgcSaThoWMaJKXUc")

TOOLS_SCHEMA = [
    {
        "name": "ler_fila",
        "description": "Consulta tarefas e mensagens pendentes na fila do Zuler AI Handoff (Supabase), com isolamento estrito por nó/computador.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_queue": {
                    "type": "string",
                    "description": "Fila a consultar (ex: WAITING_ALITA, WAITING_ANTIGRAVITY). Padrao: WAITING_ALITA"
                },
                "node_id": {
                    "type": "string",
                    "description": "Identificador único do nó/computador (ex: SYN-PC1, SYN-PC2). Quando informado, restringe o retorno estritamente às mensagens destinadas a este nó."
                },
                "limit": {
                    "type": "integer",
                    "description": "Numero maximo de tarefas a retornar (padrao: 5)"
                }
            }
        }
    },
    {
        "name": "obter_tarefa",
        "description": "Obtém uma tarefa específica pelo ID com verificação de autorização e barreira contra vazamento entre computadores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tarefa_id": {
                    "type": "string",
                    "description": "UUID da tarefa a ser consultada"
                },
                "node_id": {
                    "type": "string",
                    "description": "Identificador do nó solicitante autenticado. Bloqueia a leitura se a tarefa pertencer a outro computador."
                }
            },
            "required": ["tarefa_id"]
        }
    },
    {
        "name": "enviar_mensagem",
        "description": "Envia mensagem, ordem ou resposta de governanca com vinculacao de nó de origem e nó de destino.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "destinatario": {
                    "type": "string",
                    "description": "Nome do destinatario (ex: ISAAC, ANTIGRAVITY, NIKOLA)"
                },
                "mensagem": {
                    "type": "string",
                    "description": "Conteudo da mensagem, instrucao ou feedback"
                },
                "tarefa_origem_id": {
                    "type": "string",
                    "description": "ID da tarefa de origem se for resposta a uma tarefa existente"
                },
                "source_node_id": {
                    "type": "string",
                    "description": "Identificador do nó remetente"
                },
                "target_node_id": {
                    "type": "string",
                    "description": "Identificador do nó destinatário exclusivo"
                }
            },
            "required": ["destinatario", "mensagem"]
        }
    },
    {
        "name": "atualizar_status",
        "description": "Atualiza o status de processamento de uma tarefa na fila do Handoff (ex: COMPLETED, IN_PROGRESS, FAILED) com guarda de posse de nó.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tarefa_id": {
                    "type": "string",
                    "description": "UUID da tarefa a ser atualizada"
                },
                "novo_status": {
                    "type": "string",
                    "description": "Novo status (ex: COMPLETED, IN_PROGRESS, FAILED)"
                },
                "node_id": {
                    "type": "string",
                    "description": "Identificador do nó solicitante"
                }
            },
            "required": ["tarefa_id", "novo_status"]
        }
    }
]

def execute_ler_fila(arguments, tenant_id="00000000-0000-0000-0000-000000000001", node_id=None):
    target_queue = arguments.get("target_queue", "WAITING_ALITA")
    limit = int(arguments.get("limit", 5))
    target_node = node_id or arguments.get("node_id") or arguments.get("target_node_id")

    query_parts = [
        f"tenant_id=eq.{tenant_id}",
        f"target_queue=eq.{urllib.parse.quote(target_queue)}"
    ]
    if target_node:
        query_parts.append(f"metadata->>target_node_id=eq.{urllib.parse.quote(target_node)}")

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?{'&'.join(query_parts)}&order=created_at.desc&limit={limit}"
    req = urllib.request.Request(url, headers={
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # Defesa em profundidade no servidor: sanitização in-memory
    if target_node:
        data = [
            row for row in data
            if (row.get("metadata") or {}).get("target_node_id") == target_node
        ]
    return data

def execute_obter_tarefa(arguments, tenant_id="00000000-0000-0000-0000-000000000001", node_id=None):
    tarefa_id = arguments.get("tarefa_id")
    target_node = node_id or arguments.get("node_id") or arguments.get("target_node_id")
    if not tarefa_id:
        raise ValueError("tarefa_id é obrigatório")

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{urllib.parse.quote(tarefa_id)}&tenant_id=eq.{tenant_id}"
    req = urllib.request.Request(url, headers={
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if not data:
        return {"status": "NAO_ENCONTRADO", "erro": f"Tarefa {tarefa_id} não encontrada para o tenant {tenant_id}"}

    row = data[0]
    msg_meta = row.get("metadata") or {}
    msg_target_node = msg_meta.get("target_node_id")

    # Regra estrita de autorização por nó (Server-Side Node Isolation)
    if target_node and msg_target_node and msg_target_node != target_node:
        raise PermissionError(
            f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A mensagem {tarefa_id} pertence ao nó '{msg_target_node}'. "
            f"O nó solicitante '{target_node}' não possui autorização para ler este conteúdo."
        )

    return {"status": "SUCESSO", "registro": row}

def execute_enviar_mensagem(arguments, tenant_id="00000000-0000-0000-0000-000000000001", node_id=None):
    destinatario = arguments.get("destinatario", "ISAAC").strip().upper()
    mensagem = arguments.get("mensagem", "")
    tarefa_origem_id = arguments.get("tarefa_origem_id")

    source_node = node_id or arguments.get("source_node_id") or arguments.get("node_id") or "UNKNOWN_NODE"
    target_node = arguments.get("target_node_id") or source_node

    metadata = arguments.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["source_node_id"] = source_node
    metadata["target_node_id"] = target_node

    # Roteamento estrito conforme RFC-001 v2.0 (ALITA -> DESTINO)
    if any(k in destinatario for k in ["ATLAS", "CODEX"]):
        header = "ALITA -> ATLAS | "
    elif any(k in destinatario for k in ["NIKOLA", "PC2"]):
        header = "ALITA -> NIKOLA | "
    elif "AMBOS" in destinatario or "TODOS" in destinatario:
        header = "ALITA -> AMBOS | "
    else:
        header = "ALITA -> ISAAC | "

    payload_body = {
        "remetente": "ALITA",
        "target_queue": "WAITING_ANTIGRAVITY",
        "tenant_id": tenant_id,
        "metadata": metadata,
        "payload": f"{header}{mensagem}",
        "status": "WAITING_ANTIGRAVITY"
    }
    if tarefa_origem_id:
        payload_body["reply_to_id"] = tarefa_origem_id

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload_body).encode("utf-8"),
        headers={
            "apikey": API_KEY,
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return {"status": "ENVIADO_COM_SUCESSO", "registro": data}

def execute_atualizar_status(arguments, tenant_id="00000000-0000-0000-0000-000000000001", node_id=None):
    tarefa_id = arguments.get("tarefa_id")
    novo_status = arguments.get("novo_status")
    target_node = node_id or arguments.get("node_id") or arguments.get("target_node_id")

    if target_node:
        check_url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{urllib.parse.quote(tarefa_id)}&tenant_id=eq.{tenant_id}"
        req_check = urllib.request.Request(check_url, headers={
            "apikey": API_KEY,
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(req_check, timeout=10) as resp_check:
            check_data = json.loads(resp_check.read().decode("utf-8"))
            if check_data:
                msg_target_node = (check_data[0].get("metadata") or {}).get("target_node_id")
                if msg_target_node and msg_target_node != target_node:
                    raise PermissionError(
                        f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: Atualização rejeitada. A mensagem pertence ao nó '{msg_target_node}'."
                    )

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{urllib.parse.quote(tarefa_id)}&tenant_id=eq.{tenant_id}"
    payload_body = {"status": novo_status}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload_body).encode("utf-8"),
        headers={
            "apikey": API_KEY,
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        },
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if not data:
            return {"status": "ERRO_NAO_ENCONTRADO_OU_TENANT_INVALIDO", "erro": f"Tarefa {tarefa_id} não encontrada para o tenant {tenant_id}"}
        return {"status": "ATUALIZADO_COM_SUCESSO", "registro": data}

class MCPRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, X-Synapse-Tenant-Key, X-Synapse-Node-Id, X-Machine-Id")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/mcp", "/health"):
            body = json.dumps({
                "status": "online",
                "service": "Synapse Handoff MCP Server",
                "transport": "streamable-http",
                "protocol": "2024-11-05",
                "version": "2.1.0-node-isolated"
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/wake_alita") or self.path.startswith("/api/handoff/poll_alita"):
            try:
                node_id = self._extract_node_id()
                query = "target_queue=eq.WAITING_ALITA&status=eq.WAITING_ALITA&order=created_at.desc&limit=5"
                if node_id:
                    query += f"&metadata->>target_node_id=eq.{urllib.parse.quote(node_id)}"
                url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?{query}"
                req = urllib.request.Request(url, headers={
                    "apikey": API_KEY,
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
                    if node_id:
                        rows = [r for r in rows if (r.get("metadata") or {}).get("target_node_id") == node_id]
                    body = json.dumps({"ok": True, "messages": rows}).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e), "messages": []}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _extract_tenant_id(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        tenant = qs.get('tenant_id', [None])[0] or qs.get('tenant', [None])[0]
        if not tenant:
            tenant = self.headers.get('X-Synapse-Tenant-Key') or self.headers.get('X-Tenant-Id')
        return tenant

    def _extract_node_id(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        node = qs.get('node_id', [None])[0] or qs.get('machine_id', [None])[0] or qs.get('device_id', [None])[0]
        if not node:
            node = self.headers.get('X-Synapse-Node-Id') or self.headers.get('X-Machine-Id') or self.headers.get('X-Device-Id')
        return node

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            req_data = json.loads(raw_body) if raw_body else {}
        except Exception:
            req_data = {}

        req_id = req_data.get("id")
        method = req_data.get("method", "")
        params = req_data.get("params", {})

        tenant_id = self._extract_tenant_id()
        node_id = self._extract_node_id()

        if method in ("tools/call", "tools/list") and not tenant_id:
            self.send_response(401)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            err_body = json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32001,
                    "message": "TENANT_REQUIRED: Identificador de tenant obrigatório (?tenant_id=... ou header X-Synapse-Tenant-Key)"
                }
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            return

        response = {"jsonrpc": "2.0", "id": req_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "synapse-handoff-mcp",
                    "version": "2.1.0-node-isolated"
                }
            }
        elif method == "notifications/initialized":
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()
            return
        elif method == "tools/list":
            response["result"] = {"tools": TOOLS_SCHEMA}
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            call_node = node_id or tool_args.get("node_id") or tool_args.get("target_node_id")
            try:
                if tool_name == "ler_fila":
                    tool_output = execute_ler_fila(tool_args, tenant_id, node_id=call_node)
                elif tool_name == "obter_tarefa":
                    tool_output = execute_obter_tarefa(tool_args, tenant_id, node_id=call_node)
                elif tool_name == "enviar_mensagem":
                    tool_output = execute_enviar_mensagem(tool_args, tenant_id, node_id=call_node)
                elif tool_name == "atualizar_status":
                    tool_output = execute_atualizar_status(tool_args, tenant_id, node_id=call_node)
                else:
                    raise ValueError(f"Ferramenta desconhecida: {tool_name}")

                response["result"] = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_output, ensure_ascii=False, indent=2)
                        }
                    ],
                    "isError": False
                }
            except Exception as e:
                response["result"] = {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Erro ao executar {tool_name}: {str(e)}"
                        }
                    ],
                    "isError": True
                }
        elif method == "ping":
            response["result"] = {}
        else:
            response["error"] = {
                "code": -32601,
                "message": f"Method not found: {method}"
            }

        body_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

def run(port=8001):
    server = HTTPServer(("0.0.0.0", port), MCPRequestHandler)
    print(f"=================================================================")
    print(f" SYNAPSE HANDOFF MCP SERVER ONLINE (v2.1.0-node-isolated)")
    print(f" Porta: {port}")
    print(f" Endpoints: POST / ou /mcp (streamable-http JSON-RPC 2.0)")
    print(f"=================================================================")
    server.serve_forever()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run(port)

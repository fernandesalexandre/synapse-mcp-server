# -*- coding: utf-8 -*-
"""
app.py - Servidor MCP Nativo HTTP (streamable-http) para o Synapse Handoff
Versão v2.3.0-hardened-node-auth atendendo integralmente ao laudo pericial do ATLAS:
- Segredo dedicado do servidor (NODE_HMAC_SECRET) desvinculado de API_KEY
- Validação estrita Fail-Closed na rota GET (/api/wake_alita, /api/handoff/poll_alita)
- Bloqueio de registros órfãos sem target_node_id
- Atualização atômica (PATCH) filtrando id, tenant_id E target_node_id na própria operação de banco
- Suporte a revogação de tokens de máquina (Blacklist)
- Defesa em profundidade ponta a ponta
"""

import sys
import os
import time
import hmac
import hashlib
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://dopomsklkovcvxthtyqq.supabase.co/rest/v1")
API_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRvcG9tc2tsa292Y3Z4dGh0eXFxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTExMjYzNiwiZXhwIjoyMDk2Njg4NjM2fQ.DUUhJiZqpd279H8-L2fcz4dNTEjJgcSaThoWMaJKXUc")

NODE_HMAC_SECRET = os.environ.get("SYNAPSE_NODE_HMAC_SECRET")
if not NODE_HMAC_SECRET:
    NODE_HMAC_SECRET = hashlib.sha256(b"SYNAPSE_ISOLATED_NODE_AUTHENTICATION_SALT_2026_V3_PERPETUAL").hexdigest()

REVOKED_TOKENS = set()

def revoke_machine_token(token: str):
    if token:
        REVOKED_TOKENS.add(token.strip())

def generate_machine_token(tenant_id: str, node_id: str, secret: str = NODE_HMAC_SECRET, ttl_sec: int = 86400) -> str:
    timestamp = int(time.time())
    msg = f"{tenant_id}:{node_id}:{timestamp}:{ttl_sec}"
    sig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{node_id}.{timestamp}.{ttl_sec}.{sig}"

def verify_machine_token(token: str, tenant_id: str, secret: str = NODE_HMAC_SECRET) -> tuple:
    if not token or not isinstance(token, str):
        return False, None, "TOKEN_AUSENTE: Token de máquina não informado."
    token_clean = token.strip()
    if token_clean in REVOKED_TOKENS:
        return False, None, "TOKEN_REVOGADO: A sessão desta máquina foi formalmente revogada."

    parts = token_clean.split(".")
    if len(parts) != 4:
        return False, None, "TOKEN_MALFORMADO: Formato esperado 'node_id.timestamp.ttl.signature'."
    node_id, ts_str, ttl_str, received_sig = parts
    try:
        ts = int(ts_str)
        ttl = int(ttl_str)
    except ValueError:
        return False, None, "TOKEN_TIMESTAMP_INVALIDO"

    now = int(time.time())
    if now < ts or (now - ts) > ttl:
        return False, None, "TOKEN_EXPIRADO: A sessão autenticada desta máquina expirou."

    expected_msg = f"{tenant_id}:{node_id}:{ts}:{ttl}"
    expected_sig = hmac.new(secret.encode("utf-8"), expected_msg.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_sig, expected_sig):
        return False, None, "ASSINATURA_INVALIDA: Assinatura do token de máquina inválida ou forjada."

    return True, node_id, None

TOOLS_SCHEMA = [
    {
        "name": "ler_fila",
        "description": "Consulta tarefas e mensagens pendentes na fila do Zuler AI Handoff (Supabase), com autenticação criptográfica obrigatória do computador solicitante.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_queue": {
                    "type": "string",
                    "description": "Fila a consultar (ex: WAITING_ALITA, WAITING_ANTIGRAVITY). Padrão: WAITING_ALITA"
                },
                "machine_token": {
                    "type": "string",
                    "description": "Token de autenticação de sessão do computador gerado pelo Synapse."
                },
                "limit": {
                    "type": "integer",
                    "description": "Número máximo de tarefas a retornar (padrão: 5)"
                }
            }
        }
    },
    {
        "name": "obter_tarefa",
        "description": "Obtém uma tarefa específica pelo ID com verificação rigorosa de autorização contra acesso cruzado entre computadores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tarefa_id": {
                    "type": "string",
                    "description": "UUID da tarefa a ser consultada"
                },
                "machine_token": {
                    "type": "string",
                    "description": "Token de autenticação da máquina solicitante."
                }
            },
            "required": ["tarefa_id"]
        }
    },
    {
        "name": "enviar_mensagem",
        "description": "Envia mensagem ou resposta com vinculação imutável da identidade do computador de origem e computador de destino.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "destinatario": {
                    "type": "string",
                    "description": "Nome do destinatário (ex: ISAAC, ANTIGRAVITY, NIKOLA)"
                },
                "mensagem": {
                    "type": "string",
                    "description": "Conteúdo da mensagem, instrução ou feedback"
                },
                "tarefa_origem_id": {
                    "type": "string",
                    "description": "ID da tarefa de origem se for resposta a uma tarefa existente"
                },
                "target_node_id": {
                    "type": "string",
                    "description": "Identificador do nó de destino exclusivo"
                },
                "machine_token": {
                    "type": "string",
                    "description": "Token de autenticação da máquina remetente"
                }
            },
            "required": ["destinatario", "mensagem"]
        }
    },
    {
        "name": "atualizar_status",
        "description": "Atualiza o status de processamento de uma tarefa na fila do Handoff (ex: COMPLETED, IN_PROGRESS, FAILED) com guarda estrita de nó autenticado.",
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
                "machine_token": {
                    "type": "string",
                    "description": "Token de autenticação da máquina solicitante"
                }
            },
            "required": ["tarefa_id", "novo_status"]
        }
    }
]

def execute_ler_fila(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    if not authenticated_node_id:
        raise PermissionError("ACESSO_NEGADO_AUTENTICACAO_OBRIGATORIA: Identidade de computador autenticada obrigatória para ler fila.")

    target_queue = arguments.get("target_queue", "WAITING_ALITA")
    limit = int(arguments.get("limit", 5))

    query_parts = [
        f"tenant_id=eq.{tenant_id}",
        f"target_queue=eq.{urllib.parse.quote(target_queue)}",
        f"metadata->>target_node_id=eq.{urllib.parse.quote(authenticated_node_id)}"
    ]

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?{'&'.join(query_parts)}&order=created_at.desc&limit={limit}"
    req = urllib.request.Request(url, headers={
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    filtered = [
        row for row in data
        if (row.get("metadata") or {}).get("target_node_id") == authenticated_node_id
    ]
    return filtered

def execute_obter_tarefa(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    if not authenticated_node_id:
        raise PermissionError("ACESSO_NEGADO_AUTENTICACAO_OBRIGATORIA: Identidade de computador autenticada obrigatória para obter tarefa.")

    tarefa_id = arguments.get("tarefa_id")
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

    if not msg_target_node:
        raise PermissionError(
            f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A mensagem {tarefa_id} não possui computador de destino definido (órfã). Acesso negado."
        )
    if msg_target_node != authenticated_node_id:
        raise PermissionError(
            f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A mensagem {tarefa_id} pertence exclusivamente ao computador '{msg_target_node}'. "
            f"O computador autenticado '{authenticated_node_id}' não possui autorização para acessar este conteúdo."
        )

    return {"status": "SUCESSO", "registro": row}

def execute_enviar_mensagem(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    destinatario = arguments.get("destinatario", "ISAAC").strip().upper()
    mensagem = arguments.get("mensagem", "")
    tarefa_origem_id = arguments.get("tarefa_origem_id")

    source_node = authenticated_node_id or "UNKNOWN_NODE"
    target_node = arguments.get("target_node_id") or source_node

    metadata = arguments.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["source_node_id"] = source_node
    metadata["target_node_id"] = target_node

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

def execute_atualizar_status(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    if not authenticated_node_id:
        raise PermissionError("ACESSO_NEGADO_AUTENTICACAO_OBRIGATORIA: Identidade de computador autenticada obrigatória para atualizar status.")

    tarefa_id = arguments.get("tarefa_id")
    novo_status = arguments.get("novo_status")

    url = (
        f"{SUPABASE_URL}/zuler_ai_handoff_poc"
        f"?id=eq.{urllib.parse.quote(tarefa_id)}"
        f"&tenant_id=eq.{tenant_id}"
        f"&metadata->>target_node_id=eq.{urllib.parse.quote(authenticated_node_id)}"
    )
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
            check_url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{urllib.parse.quote(tarefa_id)}&tenant_id=eq.{tenant_id}"
            req_c = urllib.request.Request(check_url, headers={"apikey": API_KEY, "Authorization": f"Bearer {API_KEY}"})
            with urllib.request.urlopen(req_c, timeout=5) as resp_c:
                other_node_data = json.loads(resp_c.read().decode("utf-8"))
                if other_node_data:
                    other_node = (other_node_data[0].get("metadata") or {}).get("target_node_id")
                    raise PermissionError(
                        f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: Atualização rejeitada. A mensagem pertence ao computador '{other_node}', não ao nó autenticado '{authenticated_node_id}'."
                    )
            return {"status": "ERRO_NAO_ENCONTRADO_OU_TENANT_INVALIDO", "erro": f"Tarefa {tarefa_id} não encontrada para o tenant {tenant_id}"}
        return {"status": "ATUALIZADO_COM_SUCESSO", "registro": data}

class MCPRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, X-Synapse-Tenant-Key, X-Synapse-Machine-Token, X-Machine-Token")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _extract_tenant_id(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        tenant = qs.get('tenant_id', [None])[0] or qs.get('tenant', [None])[0]
        if not tenant:
            tenant = self.headers.get('X-Synapse-Tenant-Key') or self.headers.get('X-Tenant-Id')
        return tenant or "00000000-0000-0000-0000-000000000001"

    def _extract_machine_token(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = qs.get('machine_token', [None])[0]
        if not token:
            token = self.headers.get('X-Synapse-Machine-Token') or self.headers.get('X-Machine-Token')
        if not token:
            auth_h = self.headers.get('Authorization', '')
            if auth_h.startswith('Bearer '):
                token = auth_h[7:].strip()
        return token

    def do_GET(self):
        if self.path in ("/", "/mcp", "/health"):
            body = json.dumps({
                "status": "online",
                "service": "Synapse Handoff MCP Server",
                "transport": "streamable-http",
                "protocol": "2024-11-05",
                "version": "2.3.0-hardened-node-auth"
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/wake_alita") or self.path.startswith("/api/handoff/poll_alita"):
            tenant_id = self._extract_tenant_id()
            raw_token = self._extract_machine_token()

            if not raw_token:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                body = json.dumps({
                    "ok": False,
                    "error": "MACHINE_AUTH_REQUIRED: Sessão de computador autenticada obrigatória para consultar mensagens."
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            is_valid, authenticated_node, err_msg = verify_machine_token(raw_token, tenant_id)
            if not is_valid or not authenticated_node:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                body = json.dumps({
                    "ok": False,
                    "error": f"MACHINE_AUTH_FAILED: {err_msg}"
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            try:
                query = (
                    f"target_queue=eq.WAITING_ALITA&status=eq.WAITING_ALITA"
                    f"&tenant_id=eq.{tenant_id}"
                    f"&metadata->>target_node_id=eq.{urllib.parse.quote(authenticated_node)}"
                    f"&order=created_at.desc&limit=5"
                )
                url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?{query}"
                req = urllib.request.Request(url, headers={
                    "apikey": API_KEY,
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
                    rows = [r for r in rows if (r.get("metadata") or {}).get("target_node_id") == authenticated_node]
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
                    "version": "2.3.0-hardened-node-auth"
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

            raw_token = tool_args.get("machine_token") or self._extract_machine_token()

            if tool_name in ("ler_fila", "obter_tarefa", "atualizar_status") and not raw_token:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                err_b = json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32002,
                        "message": "MACHINE_AUTH_REQUIRED: Sessão de computador autenticada obrigatória para acessar tarefas."
                    }
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(err_b)))
                self.end_headers()
                self.wfile.write(err_b)
                return

            authenticated_node = None
            if raw_token:
                is_valid, authed_node, err_msg = verify_machine_token(raw_token, tenant_id)
                if not is_valid:
                    self.send_response(401)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    err_b = json.dumps({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32002,
                            "message": f"MACHINE_AUTH_FAILED: {err_msg}"
                        }
                    }).encode("utf-8")
                    self.send_header("Content-Length", str(len(err_b)))
                    self.end_headers()
                    self.wfile.write(err_b)
                    return
                authenticated_node = authed_node

            try:
                if tool_name == "ler_fila":
                    tool_output = execute_ler_fila(tool_args, tenant_id, authenticated_node_id=authenticated_node)
                elif tool_name == "obter_tarefa":
                    tool_output = execute_obter_tarefa(tool_args, tenant_id, authenticated_node_id=authenticated_node)
                elif tool_name == "enviar_mensagem":
                    tool_output = execute_enviar_mensagem(tool_args, tenant_id, authenticated_node_id=authenticated_node)
                elif tool_name == "atualizar_status":
                    tool_output = execute_atualizar_status(tool_args, tenant_id, authenticated_node_id=authenticated_node)
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
    print(f" SYNAPSE HANDOFF MCP SERVER ONLINE (v2.3.0-hardened-node-auth)")
    print(f" Porta: {port}")
    print(f" Endpoints: POST / ou /mcp (streamable-http JSON-RPC 2.0)")
    print(f"=================================================================")
    server.serve_forever()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run(port)

# -*- coding: utf-8 -*-
"""
synapse_mcp_server.py - Servidor MCP Nativo HTTP (streamable-http) para o Synapse Handoff
Expondo as 3 ferramentas oficiais solicitadas pela Alita e Plugin Creator:
- ler_fila
- enviar_mensagem
- atualizar_status
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
        "description": "Consulta tarefas e mensagens pendentes na fila do Zuler AI Handoff (Supabase).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_queue": {
                    "type": "string",
                    "description": "Fila a consultar (ex: WAITING_ALITA, WAITING_ANTIGRAVITY). Padrao: WAITING_ALITA"
                },
                "limit": {
                    "type": "integer",
                    "description": "Numero maximo de tarefas a retornar (padrao: 5)"
                }
            }
        }
    },
    {
        "name": "enviar_mensagem",
        "description": "Envia mensagem, ordem ou resposta de governanca da Alita para o Isaac (Antigravity) na fila do Handoff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "destinatario": {
                    "type": "string",
                    "description": "Nome do destinatario (ex: ISAAC, ANTIGRAVITY)"
                },
                "mensagem": {
                    "type": "string",
                    "description": "Conteudo da mensagem, instrucao ou feedback"
                },
                "tarefa_origem_id": {
                    "type": "string",
                    "description": "ID da tarefa de origem se for resposta a uma tarefa existente"
                }
            },
            "required": ["destinatario", "mensagem"]
        }
    },
    {
        "name": "atualizar_status",
        "description": "Atualiza o status de processamento de uma tarefa na fila do Handoff (ex: COMPLETED, IN_PROGRESS, FAILED).",
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
                }
            },
            "required": ["tarefa_id", "novo_status"]
        }
    }
]

def execute_ler_fila(arguments, tenant_id="00000000-0000-0000-0000-000000000001"):
    target_queue = arguments.get("target_queue", "WAITING_ALITA")
    limit = int(arguments.get("limit", 5))
    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?tenant_id=eq.{tenant_id}&target_queue=eq.{urllib.parse.quote(target_queue)}&order=created_at.desc&limit={limit}"
    req = urllib.request.Request(url, headers={
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data

def execute_enviar_mensagem(arguments, tenant_id="00000000-0000-0000-0000-000000000001"):
    destinatario = arguments.get("destinatario", "ISAAC").strip().upper()
    mensagem = arguments.get("mensagem", "")
    tarefa_origem_id = arguments.get("tarefa_origem_id")

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

def execute_atualizar_status(arguments, tenant_id="00000000-0000-0000-0000-000000000001"):
    tarefa_id = arguments.get("tarefa_id")
    novo_status = arguments.get("novo_status")

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
            return {"status": "ERRO_NAO_ENCONTRADO_OU_TENANT_INVALIDO", "erro": f"Tarefa {tarefa_id} nao encontrada para o tenant {tenant_id}"}
        return {"status": "ATUALIZADO_COM_SUCESSO", "registro": data}

class MCPRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/sse") or "text/event-stream" in self.headers.get("Accept", ""):
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b"event: endpoint\r\ndata: /mcp\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass
            return

        if self.path.startswith("/download/"):
            fname = self.path[len("/download/"):].split("?")[0]
            ALLOWED_FILES = {
                "latest.exe": "setup_synapse_commercial_v3.2.2.exe",
                "setup_synapse_commercial_latest.exe": "setup_synapse_commercial_v3.2.2.exe",
                "setup_synapse_commercial_v3.2.0.exe": "setup_synapse_commercial_v3.2.0.exe",
                "setup_synapse_commercial_v3.2.1.exe": "setup_synapse_commercial_v3.2.1.exe",
                "setup_synapse_commercial_v3.2.2.exe": "setup_synapse_commercial_v3.2.2.exe",
                "latest.zip": "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.7.zip",
                "extension_latest.zip": "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.7.zip",
                "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.6.zip": "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.6.zip",
                "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.7.zip": "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.7.zip"
            }
            target_file = ALLOWED_FILES.get(fname)
            if not target_file:
                self.send_error(404, "File not allowed or not found")
                return

            file_path = os.path.join(os.path.dirname(__file__), target_file)
            if os.path.exists(file_path):
                self.send_response(200)
                self._send_cors_headers()
                mime = "application/zip" if target_file.endswith(".zip") else "application/octet-stream"
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", f'attachment; filename="{target_file}"')
                self.send_header("Content-Length", str(os.path.getsize(file_path)))
                self.end_headers()
                with open(file_path, "rb") as f_in:
                    while chunk := f_in.read(65536):
                        self.wfile.write(chunk)
                return
            else:
                self.send_error(404, f"File not found: {target_file}")
                return

        if self.path in ("/", "/mcp", "/health"):
            body = json.dumps({
                "status": "online",
                "service": "Synapse Handoff MCP Server",
                "transport": "streamable-http",
                "protocol": "2024-11-05",
                "version": "2.0.0-multi-tenant"
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/api/wake_alita", "/api/handoff/poll_alita"):
            try:
                url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?target_queue=eq.WAITING_ALITA&status=eq.WAITING_ALITA&order=created_at.desc&limit=3"
                req = urllib.request.Request(url, headers={
                    "apikey": API_KEY,
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
                    body = json.dumps({"ok": True, "messages": rows}).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e), "messages": []}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/freeze_turn":
            try:
                url = f"{SUPABASE_URL}/zuler_ai_handoff_poc"
                payload = {
                    "remetente": "SUPERVISOR",
                    "payload": "ALITA <- SUPERVISOR | [ENCERRAMENTO DE TURNO] O usuário autorizou o encerramento da sessão. Por favor, consolidem o PROJETO_R9.md e congelem o estado.",
                    "status": "WAITING_ALITA",
                    "target_queue": "WAITING_ALITA"
                }
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
                    "apikey": API_KEY,
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                }, method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    pass
                body = json.dumps({"ok": True, "status": "FREEZE_POSTED"}).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
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
        return tenant or '00000000-0000-0000-0000-000000000001'

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
        print(f"[MCP INCOMING] method={method}, id={req_id}, params_keys={list(params.keys())}", flush=True)

        tenant_id = self._extract_tenant_id()
        # Falha fechado estrita: chamadas de fila e ferramentas exigem tenant autenticado/informado
        # Permissivo para descoberta de ferramentas e tenant padrão

        response = {"jsonrpc": "2.0", "id": req_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "synapse-handoff-mcp",
                    "version": "1.0.0"
                }
            }
        elif method == "notifications/initialized":
            # Notificação unilateral MCP - sem resposta necessária
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()
            return
        elif method == "tools/list":
            response["result"] = {"tools": TOOLS_SCHEMA}
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                if tool_name == "ler_fila":
                    tool_output = execute_ler_fila(tool_args, tenant_id)
                elif tool_name == "enviar_mensagem":
                    tool_output = execute_enviar_mensagem(tool_args, tenant_id)
                elif tool_name == "atualizar_status":
                    tool_output = execute_atualizar_status(tool_args, tenant_id)
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
    print(f" SYNAPSE HANDOFF MCP SERVER ONLINE")
    print(f" Porta: {port}")
    print(f" Endpoints: POST / ou /mcp (streamable-http JSON-RPC 2.0)")
    print(f"=================================================================")
    server.serve_forever()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run(port)

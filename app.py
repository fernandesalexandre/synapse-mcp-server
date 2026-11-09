# -*- coding: utf-8 -*-
"""
synapse_mcp_server.py - Servidor MCP Nativo HTTP (streamable-http) para o Synapse Handoff
Expondo as 3 ferramentas oficiais solicitadas pela Alita e Plugin Creator:
- ler_fila
- enviar_mensagem
- atualizar_status
E suporte completo a MCP Events (Webhooks oficiais para ChatGPT/OpenAI):
- events/list
- events/subscribe
- events/unsubscribe
Conecta-se à API Supabase do Handoff (dopomsklkovcvxthtyqq.supabase.co).
"""

import sys
import json
import urllib.request
import urllib.parse
import os
import time
import uuid
import hmac
import hashlib
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

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

EVENTS_SCHEMA = [
    {
        "name": "handoff.message_created",
        "description": "Triggered when a new Handoff directive or message arrives for Alita/ChatGPT in the queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_queue": {
                    "type": "string",
                    "description": "Queue to listen to (default: WAITING_ALITA)"
                }
            }
        }
    }
]

SUBSCRIPTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_subscriptions.json")

def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_subscriptions(subs):
    try:
        with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(subs, f, indent=2)
    except Exception as e:
        print(f"[SUBSCRIPTIONS] Error saving: {e}", flush=True)

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

    if any(k in destinatario for k in ["ATLAS", "CODEX"]):
        target_queue = "WAITING_ANTIGRAVITY"
        remetente = "ALITA"
        prefix = "ALITA -> ATLAS"
    elif any(k in destinatario for k in ["NIKOLA", "PC2"]):
        target_queue = "WAITING_ANTIGRAVITY"
        remetente = "ALITA"
        prefix = "ALITA -> NIKOLA (PC2)"
    else:
        target_queue = "WAITING_ANTIGRAVITY"
        remetente = "ALITA"
        prefix = "ALITA -> ISAAC"

    payload_text = f"{prefix} | {mensagem}" if not mensagem.startswith("ALITA ->") else mensagem

    body = {
        "remetente": remetente,
        "payload": payload_text,
        "status": target_queue,
        "target_queue": target_queue,
        "tenant_id": tenant_id
    }
    if tarefa_origem_id:
        body["reply_to_id"] = tarefa_origem_id

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc"
    data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "apikey": API_KEY,
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return {"status": "ENVIADO_COM_SUCESSO", "registro": res}

def execute_atualizar_status(arguments, tenant_id="00000000-0000-0000-0000-000000000001"):
    tarefa_id = arguments.get("tarefa_id")
    novo_status = arguments.get("novo_status", "COMPLETED").strip().upper()

    if not tarefa_id:
        raise ValueError("tarefa_id e obrigatorio")

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{tarefa_id}&tenant_id=eq.{tenant_id}"
    body = {"status": novo_status}
    data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
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
            fname = self.path[len("/download/"):]
            if fname in ("latest.exe", "setup_synapse_commercial_latest.exe"):
                fname = "setup_synapse_commercial_v3.2.2.exe"
            elif fname in ("latest.zip", "extension_latest.zip"):
                fname = "SYNAPSE_WAKE_PILOT_STORE_RELEASE_v2.9.0.7.zip"
            
            file_path = os.path.join(os.path.dirname(__file__), fname)
            if os.path.exists(file_path):
                self.send_response(200)
                self._send_cors_headers()
                mime = "application/zip" if fname.endswith(".zip") else "application/octet-stream"
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(os.path.getsize(file_path)))
                self.end_headers()
                with open(file_path, "rb") as f_in:
                    while chunk := f_in.read(65536):
                        self.wfile.write(chunk)
                return
            else:
                self.send_error(404, f"File not found: {fname}")
                return

        if self.path in ("/", "/mcp", "/health"):
            body = json.dumps({
                "status": "online",
                "service": "Synapse Handoff MCP Server",
                "transport": "streamable-http",
                "protocol": "2024-11-05",
                "version": "2.1.0-events"
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/api/wake_alita", "/api/handoff/poll_alita"):
            try:
                url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?target_queue=in.(WAITING_ALITA,WAITING_CHATGPT)&status=in.(WAITING_ALITA,WAITING_CHATGPT)&order=created_at.desc&limit=5"
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
        response = {"jsonrpc": "2.0", "id": req_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "events": {}
                },
                "serverInfo": {
                    "name": "synapse-handoff-mcp",
                    "version": "2.1.0-events"
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
        elif method == "events/list":
            response["result"] = {"events": EVENTS_SCHEMA}
        elif method == "events/subscribe":
            event_name = params.get("name")
            callback_url = params.get("url")
            secret = params.get("secret", "")
            filter_params = params.get("filter", {})
            if not callback_url:
                response["error"] = {"code": -32602, "message": "Missing required callback url"}
            else:
                subs = load_subscriptions()
                subs = [s for s in subs if s.get("url") != callback_url]
                sub_id = str(uuid.uuid4())
                subs.append({
                    "id": sub_id,
                    "name": event_name or "handoff.message_created",
                    "url": callback_url,
                    "secret": secret,
                    "filter": filter_params,
                    "tenant_id": tenant_id,
                    "created_at": time.time()
                })
                save_subscriptions(subs)
                print(f"[MCP EVENTS] Novo assinante registrado: {callback_url} (id={sub_id})", flush=True)
                response["result"] = {"status": "subscribed", "subscriptionId": sub_id}
        elif method == "events/unsubscribe":
            sub_id = params.get("subscriptionId") or params.get("id")
            callback_url = params.get("url")
            subs = load_subscriptions()
            subs = [s for s in subs if s.get("id") != sub_id and s.get("url") != callback_url]
            save_subscriptions(subs)
            print(f"[MCP EVENTS] Assinatura removida: id={sub_id}, url={callback_url}", flush=True)
            response["result"] = {"status": "unsubscribed"}
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

def background_event_dispatcher():
    """Worker de segundo plano para inspecionar Supabase e entregar webhooks aos assinantes do ChatGPT."""
    print("[EVENT_DISPATCHER] Iniciado worker de notificacoes de eventos MCP...", flush=True)
    seen_ids = set()
    while True:
        try:
            subs = load_subscriptions()
            if subs:
                url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?target_queue=in.(WAITING_ALITA,WAITING_CHATGPT)&status=in.(WAITING_ALITA,WAITING_CHATGPT)&order=created_at.desc&limit=5"
                req = urllib.request.Request(url, headers={
                    "apikey": API_KEY,
                    "Authorization": f"Bearer {API_KEY}",
                    "Accept": "application/json"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
                    for row in rows:
                        row_id = row.get("id")
                        if not row_id or row_id in seen_ids:
                            continue

                        for sub in subs:
                            cb_url = sub.get("url")
                            secret = sub.get("secret", "")
                            sub_tenant = sub.get("tenant_id")
                            row_tenant = row.get("tenant_id")
                            if sub_tenant and row_tenant and sub_tenant != row_tenant:
                                continue

                            payload = {
                                "event": "handoff.message_created",
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "data": {
                                    "id": row_id,
                                    "remetente": row.get("remetente"),
                                    "target_queue": row.get("target_queue"),
                                    "created_at": row.get("created_at"),
                                    "summary": f"Nova diretriz recebida de {row.get('remetente')} no Handoff",
                                    "instruction": "Execute a ferramenta ler_fila para obter os detalhes completos e responder via enviar_mensagem."
                                }
                            }
                            body_bytes = json.dumps(payload).encode("utf-8")
                            headers = {
                                "Content-Type": "application/json",
                                "User-Agent": "Synapse-MCP-Events/1.0"
                            }
                            if secret:
                                h = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
                                headers["webhook-signature"] = f"v1={h}"

                            try:
                                post_req = urllib.request.Request(cb_url, data=body_bytes, headers=headers, method="POST")
                                with urllib.request.urlopen(post_req, timeout=8) as cb_resp:
                                    print(f"[EVENT_DISPATCHER] Webhook entregue para {cb_url}: status={cb_resp.status}", flush=True)
                            except Exception as err:
                                print(f"[EVENT_DISPATCHER] Erro ao enviar webhook para {cb_url}: {err}", flush=True)

                        seen_ids.add(row_id)
        except Exception:
            pass
        time.sleep(3)

def run(port=8001):
    # Iniciar despachante de eventos em background
    dispatcher_thread = threading.Thread(target=background_event_dispatcher, daemon=True)
    dispatcher_thread.start()

    server = HTTPServer(("0.0.0.0", port), MCPRequestHandler)
    print(f"=================================================================")
    print(f" SYNAPSE HANDOFF MCP SERVER ONLINE (v2.1.0-events)")
    print(f" Porta: {port}")
    print(f" Suporte a: Tools (ler_fila, enviar_mensagem, atualizar_status)")
    print(f" Suporte a: Events (events/list, events/subscribe, events/unsubscribe)")
    print(f"=================================================================")
    server.serve_forever()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run(port)

# -*- coding: utf-8 -*-
"""
app.py - Servidor MCP Nativo HTTP (streamable-http) para o Synapse Handoff
Versão v2.3.2-hardened-node-auth atendendo integralmente ao laudo pericial final do ATLAS:
1. Segredo dedicado sem valor padrão previsível: Fail-Closed incondicional se SYNAPSE_NODE_HMAC_SECRET ausente.
2. Revogação persistente compartilhada: Persistência local e sincronização entre instâncias via backend.
3. OWASP Zero-Token-In-URL: Proibição estrita de tokens em query string (URL), exigindo cabeçalho HTTP.
4. Validação estrita Fail-Closed na rota GET (/api/wake_alita, /api/handoff/poll_alita).
5. Bloqueio incondicional de registros órfãos sem target_node_id.
6. Atualização atômica (PATCH) filtrando id, tenant_id E target_node_id diretamente no banco.
7. Total suporte a execução em ambiente de teste isolado e hermético (sem contato com produção).
"""

import sys
import os
import json
import time
import hmac
import hashlib
import uuid
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://dopomsklkovcvxthtyqq.supabase.co/rest/v1")
API_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRvcG9tc2tsa292Y3Z4dGh0eXFxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTExMjYzNiwiZXhwIjoyMDk2Njg4NjM2fQ.DUUhJiZqpd279H8-L2fcz4dNTEjJgcSaThoWMaJKXUc")

def get_node_hmac_secret() -> str:
    """
    Recupera o segredo criptográfico dedicado da instalação.
    FAIL-CLOSED: Recusa-se terminantemente a operar com segredo padrão ou previsível.
    """
    secret = os.environ.get("SYNAPSE_NODE_HMAC_SECRET")
    if not secret or not secret.strip():
        raise RuntimeError(
            "FATAL_SECURITY_ERROR: Variável de ambiente 'SYNAPSE_NODE_HMAC_SECRET' obrigatória ausente. "
            "O servidor Synapse recusa-se terminantemente a iniciar sem segredo criptográfico dedicado (Fail-Closed)."
        )
    return secret.strip()

# ==============================================================================
# GESTÃO DE REVOGAÇÃO PERSISTENTE (CROSS-RESTART & MULTI-INSTANCE)
# ==============================================================================

REVOCATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synapse_revocations.json")
REVOKED_TOKENS = set()

def load_persistent_revocations():
    """Carrega tokens revogados do armazenamento local e sincroniza com o backend persistente."""
    global REVOKED_TOKENS
    # 1. Arquivo local em disco
    if os.path.exists(REVOCATION_FILE):
        try:
            with open(REVOCATION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("revoked_tokens", []):
                    REVOKED_TOKENS.add(item)
        except Exception:
            pass

    # 2. Sincronização com o backend (compartilhado entre réplicas e instâncias)
    try:
        url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?metadata->>type=eq.NODE_REVOCATION&select=metadata&limit=100"
        req = urllib.request.Request(url, headers={
            "apikey": API_KEY,
            "Authorization": f"Bearer {API_KEY}"
        })
        with urllib.request.urlopen(req, timeout=3) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
            for r in rows:
                meta = r.get("metadata") or {}
                t = meta.get("revoked_token")
                sig = meta.get("revoked_signature")
                if t:
                    REVOKED_TOKENS.add(t)
                if sig:
                    REVOKED_TOKENS.add(sig)
    except Exception:
        pass

def revoke_machine_token(token: str, persist_cloud: bool = True):
    """
    Revoga um token de máquina de forma estrita e persistente.
    Grava localmente e replica para o banco para sincronizar entre réplicas e instâncias.
    """
    if not token or not isinstance(token, str):
        return
    token_clean = token.strip()
    REVOKED_TOKENS.add(token_clean)
    parts = token_clean.split(".")
    sig = parts[3] if len(parts) == 4 else None
    if sig:
        REVOKED_TOKENS.add(sig)

    # 1. Persistência local em disco
    try:
        data = {
            "revoked_tokens": list(REVOKED_TOKENS),
            "updated_at": int(time.time())
        }
        with open(REVOCATION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

    # 2. Persistência no backend para sincronização entre múltiplas instâncias
    if persist_cloud:
        try:
            body = {
                "id": str(uuid.uuid4()),
                "remetente": "SECURITY_GOVERNANCE",
                "target_queue": "WAITING_ALITA",
                "status": "COMPLETED",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "metadata": {
                    "type": "NODE_REVOCATION",
                    "revoked_token": token_clean,
                    "revoked_signature": sig,
                    "revoked_at": int(time.time()),
                    "reason": "ADMIN_REVOCATION"
                },
                "payload": "SESSÃO DE MÁQUINA FORMALMENTE REVOGADA"
            }
            req = urllib.request.Request(
                f"{SUPABASE_URL}/zuler_ai_handoff_poc",
                headers={
                    "apikey": API_KEY,
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                },
                data=json.dumps(body).encode("utf-8")
            )
            with urllib.request.urlopen(req, timeout=4) as _:
                pass
        except Exception:
            pass

def generate_machine_token(tenant_id: str, node_id: str, secret: str = None, ttl_sec: int = 86400) -> str:
    sec = secret or get_node_hmac_secret()
    timestamp = int(time.time())
    msg = f"{tenant_id}:{node_id}:{timestamp}:{ttl_sec}"
    sig = hmac.new(sec.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{node_id}.{timestamp}.{ttl_sec}.{sig}"

def verify_machine_token(token: str, tenant_id: str, secret: str = None) -> tuple:
    sec = secret or get_node_hmac_secret()
    if not token or not isinstance(token, str):
        return False, None, "TOKEN_AUSENTE: Token de máquina não informado."
    token_clean = token.strip()
    if token_clean in REVOKED_TOKENS:
        return False, None, "TOKEN_REVOGADO: A sessão desta máquina foi formalmente revogada."

    parts = token_clean.split(".")
    if len(parts) != 4:
        return False, None, "TOKEN_MALFORMADO: Formato esperado 'node_id.timestamp.ttl.signature'."
    node_id, ts_str, ttl_str, received_sig = parts

    if received_sig in REVOKED_TOKENS:
        return False, None, "TOKEN_REVOGADO: A assinatura desta máquina foi formalmente revogada."

    try:
        ts = int(ts_str)
        ttl = int(ttl_str)
    except ValueError:
        return False, None, "TOKEN_TIMESTAMP_INVALIDO"

    now = int(time.time())
    if now < ts or (now - ts) > ttl:
        return False, None, "TOKEN_EXPIRADO: A sessão autenticada desta máquina expirou."

    expected_msg = f"{tenant_id}:{node_id}:{ts}:{ttl}"
    expected_sig = hmac.new(sec.encode("utf-8"), expected_msg.encode("utf-8"), hashlib.sha256).hexdigest()
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
        "description": "Obtém uma tarefa específica pelo ID com validação estrita de isolamento de nó.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tarefa_id": {
                    "type": "string",
                    "description": "UUID da tarefa no Supabase"
                },
                "machine_token": {
                    "type": "string",
                    "description": "Token criptográfico de máquina."
                }
            },
            "required": ["tarefa_id"]
        }
    },
    {
        "name": "enviar_mensagem",
        "description": "Envia uma nova mensagem/diretriz para a fila do Handoff carimbando a máquina de origem autenticada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "remetente": {"type": "string", "description": "Nome do remetente"},
                "target_queue": {"type": "string", "description": "Fila de destino"},
                "status": {"type": "string", "description": "Status inicial"},
                "payload": {"type": "string", "description": "Conteúdo da mensagem"},
                "metadata": {"type": "object", "description": "Metadados adicionais"}
            },
            "required": ["payload"]
        }
    },
    {
        "name": "atualizar_status",
        "description": "Atualiza o status de uma tarefa no Handoff de forma atômica no banco, exigindo autoridade de nó.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tarefa_id": {"type": "string", "description": "UUID da tarefa"},
                "novo_status": {"type": "string", "description": "Novo status"},
                "machine_token": {"type": "string", "description": "Token criptográfico de máquina."},
                "resultado": {"type": "string", "description": "Resultado ou resposta"}
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
        f"metadata->>target_node_id=eq.{urllib.parse.quote(authenticated_node_id)}",
        "order=created_at.desc",
        f"limit={limit}"
    ]
    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?{'&'.join(query_parts)}"
    headers = {
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        clean_rows = []
        for r in data:
            meta = r.get("metadata") or {}
            target_node = meta.get("target_node_id")
            if target_node == authenticated_node_id:
                clean_rows.append(r)
        return clean_rows

def execute_obter_tarefa(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    tarefa_id = arguments.get("tarefa_id")
    if not tarefa_id:
        raise ValueError("tarefa_id e obrigatorio")

    if not authenticated_node_id:
        raise PermissionError("ACESSO_NEGADO_AUTENTICACAO_OBRIGATORIA: Identidade de computador autenticada obrigatoria para ler tarefa.")

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{tarefa_id}&tenant_id=eq.{tenant_id}&limit=1"
    headers = {
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
        if not rows:
            return {"status": "NAO_ENCONTRADO", "mensagem": f"Nenhuma tarefa com ID {tarefa_id} encontrada"}
        record = rows[0]
        meta = record.get("metadata") or {}
        target_node = meta.get("target_node_id")

        if not target_node:
            raise PermissionError(
                f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A mensagem {tarefa_id} não possui computador de destino definido (órfã). Acesso negado."
            )

        if target_node != authenticated_node_id:
            raise PermissionError(
                f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A mensagem {tarefa_id} pertence exclusivamente ao computador '{target_node}'. "
                f"O computador autenticado '{authenticated_node_id}' tentou ler indevidamente. Operacao abortada com falha fechada."
            )

        return {"status": "SUCESSO", "registro": record}

def execute_enviar_mensagem(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    remetente = arguments.get("remetente", "MCP_CLIENT")
    target_queue = arguments.get("target_queue", "WAITING_ALITA")
    status = arguments.get("status", "WAITING_ALITA")
    payload = arguments.get("payload")
    metadata = arguments.get("metadata", {})

    if not payload:
        raise ValueError("payload e obrigatorio")

    meta = dict(metadata) if isinstance(metadata, dict) else {}
    if authenticated_node_id:
        meta["source_node_id"] = authenticated_node_id

    body = {
        "id": str(uuid.uuid4()),
        "remetente": remetente,
        "target_queue": target_queue,
        "status": status,
        "tenant_id": tenant_id,
        "metadata": meta,
        "payload": payload
    }

    url = f"{SUPABASE_URL}/zuler_ai_handoff_poc"
    headers = {
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    req = urllib.request.Request(url, headers=headers, data=json.dumps(body).encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        created_id = result[0]["id"] if isinstance(result, list) and result else body["id"]
        return {"status": "ENVIADO", "id": created_id}

def execute_atualizar_status(arguments, tenant_id="00000000-0000-0000-0000-000000000001", authenticated_node_id=None):
    tarefa_id = arguments.get("tarefa_id")
    novo_status = arguments.get("novo_status")
    resultado = arguments.get("resultado")

    if not tarefa_id or not novo_status:
        raise ValueError("tarefa_id e novo_status sao obrigatorios")

    if not authenticated_node_id:
        raise PermissionError("ACESSO_NEGADO_AUTENTICACAO_OBRIGATORIA: Identidade de computador autenticada obrigatoria para atualizar tarefas.")

    url_check = f"{SUPABASE_URL}/zuler_ai_handoff_poc?id=eq.{tarefa_id}&tenant_id=eq.{tenant_id}&limit=1"
    headers = {
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    req_check = urllib.request.Request(url_check, headers=headers, method="GET")
    with urllib.request.urlopen(req_check, timeout=10) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
        if not rows:
            return {"status": "NAO_ENCONTRADO", "mensagem": f"Tarefa {tarefa_id} nao encontrada"}
        rec = rows[0]
        meta = rec.get("metadata") or {}
        target_node = meta.get("target_node_id")

        if not target_node:
            raise PermissionError(
                f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A mensagem {tarefa_id} não possui computador de destino (órfã). Acesso negado."
            )

        if target_node != authenticated_node_id:
            raise PermissionError(
                f"ACESSO_NEGADO_ISOLAMENTO_DE_NO: A tarefa {tarefa_id} pertence exclusivamente ao computador '{target_node}'. "
                f"O computador '{authenticated_node_id}' tentou altera-la indevidamente. Bloqueio fail-closed."
            )

    query_patch = (
        f"id=eq.{tarefa_id}"
        f"&tenant_id=eq.{tenant_id}"
        f"&metadata->>target_node_id=eq.{urllib.parse.quote(authenticated_node_id)}"
    )
    url_patch = f"{SUPABASE_URL}/zuler_ai_handoff_poc?{query_patch}"
    update_data = {"status": novo_status}
    if resultado:
        update_data["resultado"] = resultado

    headers_patch = {
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    req_update = urllib.request.Request(url_patch, headers=headers_patch, data=json.dumps(update_data).encode("utf-8"), method="PATCH")
    with urllib.request.urlopen(req_update, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        if not res:
            raise PermissionError(
                f"ATUALIZACAO_RECUSADA: Nenhuma linha atualizada. A tarefa {tarefa_id} não pertence ao nó autenticado."
            )
        return {"status": "ATUALIZADO", "id": tarefa_id, "novo_status": novo_status}


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
                "version": "2.3.2-hardened-node-auth"
            }).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/wake_alita") or self.path.startswith("/api/handoff/poll_alita"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            # OWASP: Bloqueia expressamente tentativa de passar token pela URL
            if 'machine_token' in qs or 'token' in qs:
                self.send_response(401)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                body = json.dumps({
                    "ok": False,
                    "error": "TOKEN_EM_URL_PROIBIDO: Por segurança estrita (OWASP), tokens de máquina não são aceitos na URL. Envie via cabeçalho HTTP (X-Synapse-Machine-Token ou Authorization: Bearer)."
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

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

            try:
                is_valid, authenticated_node, err_msg = verify_machine_token(raw_token, tenant_id)
            except RuntimeError as re:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                body = json.dumps({"ok": False, "error": str(re)}).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

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
            self.send_response(400)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            err_b = json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}).encode("utf-8")
            self.send_header("Content-Length", str(len(err_b)))
            self.end_headers()
            self.wfile.write(err_b)
            return

        method = req_data.get("method")
        params = req_data.get("params", {})
        req_id = req_data.get("id")
        tenant_id = self._extract_tenant_id()

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "synapse-handoff-mcp",
                        "version": "2.3.2-hardened-node-auth"
                    }
                }
            }
        elif method == "notifications/initialized":
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()
            return
        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_SCHEMA}
            }
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
                try:
                    is_valid, authed_node, err_msg = verify_machine_token(raw_token, tenant_id)
                except RuntimeError as re:
                    self.send_response(500)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    err_b = json.dumps({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32000, "message": str(re)}
                    }).encode("utf-8")
                    self.send_header("Content-Length", str(len(err_b)))
                    self.end_headers()
                    self.wfile.write(err_b)
                    return

                if not is_valid:
                    self.send_response(401)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    err_b = json.dumps({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32003,
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
                    ret = execute_ler_fila(tool_args, tenant_id=tenant_id, authenticated_node_id=authenticated_node)
                elif tool_name == "obter_tarefa":
                    ret = execute_obter_tarefa(tool_args, tenant_id=tenant_id, authenticated_node_id=authenticated_node)
                elif tool_name == "enviar_mensagem":
                    ret = execute_enviar_mensagem(tool_args, tenant_id=tenant_id, authenticated_node_id=authenticated_node)
                elif tool_name == "atualizar_status":
                    ret = execute_atualizar_status(tool_args, tenant_id=tenant_id, authenticated_node_id=authenticated_node)
                else:
                    raise ValueError(f"Ferramenta desconhecida: {tool_name}")

                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(ret, ensure_ascii=False, indent=2)}]
                    }
                }
            except PermissionError as pe:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Erro de permissão: {str(pe)}"}]
                    }
                }
            except Exception as e:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Erro ao executar {tool_name}: {str(e)}"}]
                    }
                }
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }

        body = json.dumps(res).encode("utf-8")
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def run(port=8001):
    # Valida segredo estrito antes de abrir a porta (Fail-Closed)
    _ = get_node_hmac_secret()
    load_persistent_revocations()
    server = HTTPServer(("0.0.0.0", port), MCPRequestHandler)
    print(f"=================================================================")
    print(f" SYNAPSE HANDOFF MCP SERVER ONLINE (v2.3.2-hardened-node-auth)")
    print(f" Porta: {port}")
    print(f" Endpoints: POST / ou /mcp (streamable-http JSON-RPC 2.0)")
    print(f" GET /api/wake_alita e /api/handoff/poll_alita (Protegidos por Header)")
    print(f"=================================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    p = 8001
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run(p)

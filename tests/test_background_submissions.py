"""Every place Editarr starts background work is classified, and handlers use one helper.

`SITES` is the background-submission audit: each call that starts a thread,
process, executor job, asyncio task, or Starlette background task (directly or
through `functools.partial`), by module, enclosing function, and primitive, with
the authority it runs under. A new site fails until it is classified here. Under
`scanner/http` only `background.py` may start detached work, so a request handler
cannot submit work without naming its initiator's gate or a `SystemWork` reason.
Only calls on the imported `scanner.http.background` module are the helper; a
local variable that happens to share its name is not. Request-scoped asyncio
tasks are awaited and drained before the response, so they run with the
request's own principal. The standalone Qwen replay CLI serves a separate
loopback app, with shutdown handling for its two named background sites.

`SYSTEM_WORK_CALLERS` names the one place each `SystemWork` reason may be used,
so a handler cannot borrow a system reason for work a user started.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from scanner.http.background import SystemWork

ROOT = Path(__file__).resolve().parents[1]
HTTP = "scanner/http/"
HELPER_MODULE = "scanner.http.background"
HELPER = "helper: binds the initiator's gate or a named SystemWork"
REQUEST_SCOPED = "request-scoped: awaited and drained inside the request"
SYSTEM = "system: schedule, startup, or launcher with no initiating user"
WORKER = "worker process: runs under the scan's JobGate"
LOCAL_REPLAY = "standalone replay CLI: owned and reaped by the loopback app"
PRIMITIVES = frozenset(
    {"Thread", "Timer", "Process", "Popen", "create_task", "ensure_future", "run_in_executor"}
    | {"add_task", "start_soon", "submit", "BackgroundTask"}
)

LOCAL_REPLAY_SITES = {
    ("scanner/http/qwen_language_replay_process.py", "ReplayProcess._start", "Popen"): (
        1,
        LOCAL_REPLAY,
    ),
    ("scanner/http/qwen_language_replay_web.py", "create_app.run", "Thread"): (1, LOCAL_REPLAY),
}

SITES: dict[tuple[str, str, str], tuple[int, str]] = {
    ("scanner/http/background.py", "start_thread", "Thread"): (1, HELPER),
    ("scanner/http/background.py", "submit", "submit"): (1, HELPER),
    ("scanner/http/drained_threads.py", "drained_thread", "create_task"): (1, REQUEST_SCOPED),
    ("scanner/integrations/community/publications.py", "PublicationOutbox.run", "submit"): (
        1,
        "service call: MasterService.submit awaits the signed upload inside the request",
    ),
    ("scanner/backup/schedule_runner.py", "BackupScheduler.__init__", "Thread"): (1, SYSTEM),
    ("scanner/runtime/export_regeneration.py", "ExportRegenerationScheduler.__init__", "Thread"): (
        1,
        SYSTEM,
    ),
    ("scanner/runtime/apple_speech/compiler_watchdog.py", "run_compiler", "Popen"): (
        1,
        "source compilation: drained by the watchdog bound to the scan worker or local build",
    ),
    ("scanner/runtime/jellyfin_sync_scheduler.py", "JellyfinSyncScheduler.__init__", "Thread"): (
        1,
        SYSTEM,
    ),
    ("scanner/runtime/native_lifecycle.py", "_open_logs_directory", "Popen"): (2, SYSTEM),
    ("scanner/runtime/native_lifecycle.py", "NativeInstance._become_owner", "Thread"): (1, SYSTEM),
    ("scanner/runtime/process_control.py", "WorkerControlServer.start", "Thread"): (2, WORKER),
    ("scanner/runtime/process_control.py", "ProcessControl.launch_worker", "Popen"): (1, SYSTEM),
    ("scanner/runtime/restart_service.py", "_HttpChildEndpoint.accept", "Thread"): (1, SYSTEM),
    ("scanner/runtime/restart_service.py", "_launch_http_child", "Popen"): (1, SYSTEM),
    ("scanner/worker.py", "_prevent_idle_system_sleep", "Popen"): (1, WORKER),
    ("scanner/worker.py", "_run_scan_worker", "Thread"): (1, WORKER),
    ("scanner/pipeline/processes.py", "run_command", "Popen"): (1, WORKER),
    ("scanner/pipeline/processes.py", "run_command", "Thread"): (1, WORKER),
    ("scanner/pipeline/runner_workers.py", "_scan_audio_isolated", "Process"): (1, WORKER),
    ("scanner/pipeline/runner_workers.py", "_scan_visual_isolated", "Process"): (1, WORKER),
} | LOCAL_REPLAY_SITES

SYSTEM_WORK_CALLERS: dict[SystemWork, set[tuple[str, str]]] = {
    SystemWork.SCAN_DISPATCH: {("scanner/http/api_queue.py", "_start_scan_queue_worker_if_needed")},
    SystemWork.SETUP_RESTORE: {
        ("scanner/http/setup_restore_api.py", "preflight"),
        ("scanner/http/setup_restore_api.py", "activate"),
    },
}


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (parent := _dotted(node.value)):
        return f"{parent}.{node.attr}"
    return None


def _helper_names(tree: ast.Module, path: str) -> set[str]:
    """Names in this module that are bound only to the `scanner.http.background` import."""

    package = path.removesuffix(".py").replace("/", ".").rsplit(".", 1)[0]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(filter(None, [package.rsplit(".", node.level - 1)[0], base]))
            names |= {
                alias.asname or alias.name
                for alias in node.names
                if f"{base}.{alias.name}" == HELPER_MODULE
            }
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == HELPER_MODULE:
                    names.add(alias.asname or HELPER_MODULE)
    # Any other binding of the name (a parameter, assignment, or loop target) could be anything.
    rebound = {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)} | {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    return names - rebound


def _callee_primitive(callee: ast.expr, helper: set[str]) -> str | None:
    if isinstance(callee, ast.Name):
        # A local `submit()` is an ordinary function; only `executor.submit` queues work.
        return callee.id if callee.id in PRIMITIVES - {"submit"} else None
    if not isinstance(callee, ast.Attribute) or callee.attr not in PRIMITIVES:
        return None
    return None if _dotted(callee.value) in helper else callee.attr


def _primitive(call: ast.Call, helper: set[str]) -> str | None:
    if _dotted(call.func) in {"partial", "functools.partial"} and call.args:
        return _callee_primitive(call.args[0], helper)
    return _callee_primitive(call.func, helper)


def _scoped(tree: ast.AST):
    """Yield every node with the dotted name of its enclosing class and function scope."""

    def walk(node: ast.AST, scope: tuple[str, ...]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, (*scope, child.name))
                continue
            yield ".".join(scope) or "<module>", child
            yield from walk(child, scope)

    yield from walk(tree, ())


def sites_in(source: str, path: str) -> Counter:
    """Count background-starting calls in one module by enclosing function."""

    tree = ast.parse(source)
    helper = _helper_names(tree, path)
    return Counter(
        (path, scope, primitive)
        for scope, node in _scoped(tree)
        if isinstance(node, ast.Call) and (primitive := _primitive(node, helper))
    )


def _is_system_work(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name | ast.Attribute)
        and (_dotted(node) or "").split(".")[-1] == "SystemWork"
    )


def system_work_in(source: str, path: str) -> Counter:
    """Count each `SystemWork` reference by enclosing function; non-member uses count as `?`."""

    if path == "scanner/http/background.py":
        return Counter()
    found: Counter = Counter()
    enum_nodes = []
    member_receivers = set()
    for scope, node in _scoped(ast.parse(source)):
        if _is_system_work(node):
            enum_nodes.append((scope, node))
        elif isinstance(node, ast.Attribute) and _is_system_work(node.value):
            found[(path, scope, node.attr)] += 1
            member_receivers.add(id(node.value))
    for scope, node in enum_nodes:
        if id(node) not in member_receivers:
            found[(path, scope, "?")] += 1
    return found


def scanned(census=sites_in) -> Counter:
    found: Counter = Counter()
    for module in sorted((ROOT / "scanner").rglob("*.py")):
        found += census(module.read_text(encoding="utf-8"), module.relative_to(ROOT).as_posix())
    return found


def violations(found: Counter, sites=SITES) -> list[str]:
    problems = []
    for site, count in sorted(found.items()):
        registered = sites.get(site)
        if registered is None or registered[0] != count:
            problems.append(f"unclassified background work: {site} x{count}")
        elif (
            site[0].startswith(HTTP)
            and registered[1] not in {HELPER, REQUEST_SCOPED}
            and registered != LOCAL_REPLAY_SITES.get(site)
        ):
            problems.append(f"request path starts background work outside the helper: {site}")
    problems.extend(f"stale classification: {site}" for site in sites.keys() - found.keys())
    return problems


def system_work_violations(found: Counter, callers=SYSTEM_WORK_CALLERS) -> list[str]:
    allowed = {
        (path, scope, reason.name) for reason, sites in callers.items() for path, scope in sites
    }
    problems = [
        f"SystemWork used outside its caller: {site}"
        for site in sorted(found)
        if site not in allowed
    ]
    problems.extend(f"stale SystemWork caller: {site}" for site in sorted(allowed - found.keys()))
    return problems


def test_every_background_submission_is_classified():
    assert violations(scanned()) == []


def test_every_system_work_reason_is_used_only_by_its_caller():
    assert SYSTEM_WORK_CALLERS.keys() == set(SystemWork)
    assert system_work_violations(scanned(system_work_in)) == []


def test_a_handler_starting_its_own_thread_or_executor_job_fails_the_check():
    fabricated = """
from threading import Thread

def api_start(request, caller):
    Thread(target=lambda: None, daemon=True).start()
    _executor.submit(work)
    background.start_thread("ok", gate, work)
"""
    found = scanned() + sites_in(fabricated, "scanner/http/fabricated_api.py")
    assert violations(found) == [
        "unclassified background work: "
        "('scanner/http/fabricated_api.py', 'api_start', 'Thread') x1",
        "unclassified background work: "
        "('scanner/http/fabricated_api.py', 'api_start', 'submit') x1",
    ]


def test_a_handler_site_cannot_be_waved_through_as_system_work():
    site = ("scanner/http/fabricated_api.py", "api_start", "Thread")
    found = scanned() + Counter({site: 1})
    assert violations(found, SITES | {site: (1, SYSTEM)}) == [
        f"request path starts background work outside the helper: {site}"
    ]


def test_local_replay_classification_is_limited_to_its_two_sites():
    site = ("scanner/http/fabricated_api.py", "api_start", "Thread")
    found = scanned() + Counter({site: 1})
    assert violations(found, SITES | {site: (1, LOCAL_REPLAY)}) == [
        f"request path starts background work outside the helper: {site}"
    ]


def test_only_the_imported_helper_module_is_exempt():
    fabricated = """
import functools
import threading
from fastapi import BackgroundTasks
from starlette.background import BackgroundTask
from . import background
from scanner.http import background as helper

def api_queue(request, background: BackgroundTasks):
    background.add_task(work)

def api_task():
    return BackgroundTask(work)

def api_partial():
    functools.partial(threading.Thread, target=work)().start()
    helper.submit(_executor, gate, work)
"""
    found = scanned() + sites_in(fabricated, "scanner/http/fabricated_api.py")
    assert violations(found) == [
        "unclassified background work: "
        "('scanner/http/fabricated_api.py', 'api_partial', 'Thread') x1",
        "unclassified background work: "
        "('scanner/http/fabricated_api.py', 'api_queue', 'add_task') x1",
        "unclassified background work: "
        "('scanner/http/fabricated_api.py', 'api_task', 'BackgroundTask') x1",
    ]


def test_a_handler_borrowing_a_system_work_reason_fails_the_check():
    fabricated = """
from . import background
from scanner.http.background import SystemWork

def api_start(request):
    background.submit(_executor, background.SystemWork.SETUP_RESTORE, work)

def api_dynamic(request, name):
    background.start_thread("x", getattr(SystemWork, name), work)
"""
    found = scanned(system_work_in) + system_work_in(fabricated, "scanner/http/fabricated_api.py")
    assert system_work_violations(found) == [
        "SystemWork used outside its caller: "
        "('scanner/http/fabricated_api.py', 'api_dynamic', '?')",
        "SystemWork used outside its caller: "
        "('scanner/http/fabricated_api.py', 'api_start', 'SETUP_RESTORE')",
    ]

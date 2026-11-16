"""Application startup recovery and scheduler shutdown."""

import logging

logger = logging.getLogger(__name__)


def _secure_data_directory() -> None:
    """Create the service-owned data directory before writing authentication state."""
    from scanner import api as root

    root.ensure_user_private_directory(root.settings.data_dir)


def _recover_and_start_scan_queue_processing() -> None:
    """Run configured-instance recovery and start one scheduler for this lifecycle."""
    from scanner import api as root

    with root.SCAN_QUEUE_ACTIVATION_LOCK:
        if root.SCAN_QUEUE_PROCESSING_ACTIVE:
            return
        factory = root.get_session_factory()
        db = factory()
        try:
            root.recover_cancelled_scan_queue_items(db)
            root.recover_interrupted_credits_model_acquisitions(db)
            root.recover_terminal_scan_queue_items(db)
            active_pids = set()
            for scan_id, pid, pgid, token in root.scanning_scan_worker_processes(db):
                identity = root._worker_identity(scan_id, pid, pgid, token)
                if identity is None:
                    root.cleanup_worker_control_endpoint(scan_id, token)
                else:
                    active_pids.add(identity.pid)
            root.mark_orphaned_scan_workers(db, active_pids)
        finally:
            db.close()
        root.SCAN_QUEUE_STOP_EVENT.clear()
        root.SCAN_QUEUE_WAKE_EVENT.clear()
        root._start_scan_queue_worker_if_needed()
        root.SCAN_QUEUE_PROCESSING_ACTIVE = True
        from scanner.backup.schedule_runner import BackupScheduler
        from scanner.persistence.migrations.preflight import database_location

        database_path, data_dir = database_location()
        scheduler = BackupScheduler(database_path, data_dir)
        root.app.state.backup_scheduler = scheduler
        scheduler.start()
        from scanner.runtime.jellyfin_sync_scheduler import JellyfinSyncScheduler

        jellyfin_sync = JellyfinSyncScheduler(factory)
        root.app.state.jellyfin_sync_scheduler = jellyfin_sync
        jellyfin_sync.start()
        from scanner.runtime.export_regeneration import ExportRegenerationScheduler

        export_regeneration = ExportRegenerationScheduler(factory, data_dir=root.settings.data_dir)
        root.app.state.export_regeneration_scheduler = export_regeneration
        export_regeneration.start()


def startup():
    """Initialize runtime state and recover persisted scan work on application startup."""
    from scanner import api as root
    from scanner.backup.activation import GENERATION, JOURNAL, recover_restore
    from scanner.persistence.migrations.preflight import database_location

    database_path, data_dir = database_location()
    if (data_dir / JOURNAL).exists() or (data_dir / GENERATION).exists():
        recover_restore(database_path, data_dir)
    # Deployment resolution happens in the launch path before bind.  Read only the
    # fixed snapshot here; persistence changes must not mutate this generation.
    root.active_deployment_snapshot()
    root._secure_data_directory()
    root.ensure_user_private_directory(root.settings.log_dir)
    try:
        diagnostics = root.load_diagnostics_preferences(root.settings.data_dir)
    except root.DiagnosticsError:
        diagnostics = root.DiagnosticsPreferences()
        root.logger.warning("Diagnostics root.settings are unavailable; using info logging")
    root.configure_runtime_logging(root.settings.log_dir, debug_enabled=diagnostics.debug_enabled)
    root.init_db()
    root.settings.exports_dir.mkdir(parents=True, exist_ok=True)
    csrf_encryption_key = root.load_csrf_encryption_key(root.settings.data_dir)
    api_key_wrap_key = root.load_api_key_wrap_key(root.settings.data_dir)
    audit = root.AuthenticationAuditLogger(root.settings.log_dir)
    root.app.state.authentication_audit = audit
    factory = root.get_session_factory()
    db = factory()
    try:
        root.run_audit_retention(db)
        root.seal_plaintext_api_keys(db, api_key_wrap_key)
        revoked_sessions = root.revoke_sessions_with_other_csrf_key_id(
            db, csrf_encryption_key.key_id, commit=False
        )
        if revoked_sessions:
            # An audit failure aborts startup rather than serve after an unrecorded revocation.
            root.record_key_change_revocation(db, revoked_sessions)
            db.commit()
            audit.emit(
                event="auth.sessions_revoked",
                outcome="success",
                severity="info",
                context=root.AuditContext(source="system", request_id=root.new_request_id()),
                actor="system",
            )
        root.cleanup_expired_sessions(db)
        setup_required = root.setup_required(db)
        root.app.state.setup_required = setup_required
    finally:
        db.close()
    if (
        not setup_required
        and not getattr(root.app.state, "defer_scan_queue_activation", False)
        and not (root.settings.data_dir / "restore-reconciliation.json").exists()
    ):
        root._recover_and_start_scan_queue_processing()


def shutdown():
    """Stop scheduling; dedicated scan workers survive an API reload."""
    from scanner import api as root

    scheduler = getattr(root.app.state, "backup_scheduler", None)
    if scheduler is not None:
        scheduler.stop()
        root.app.state.backup_scheduler = None
    export_regeneration = getattr(root.app.state, "export_regeneration_scheduler", None)
    if export_regeneration is not None:
        if export_regeneration.stop():
            root.app.state.export_regeneration_scheduler = None
        else:
            logger.warning("Export regeneration is still draining its admitted write")
    jellyfin_sync = getattr(root.app.state, "jellyfin_sync_scheduler", None)
    if jellyfin_sync is not None:
        if jellyfin_sync.stop():
            root.app.state.jellyfin_sync_scheduler = None
        else:
            logger.warning("Jellyfin account sync is still finishing a read; it will not write")
    root.SCAN_QUEUE_STOP_EVENT.set()
    root.SCAN_QUEUE_WAKE_EVENT.set()
    with root.SCAN_QUEUE_WORKER_LOCK:
        worker = root.SCAN_QUEUE_WORKER_THREAD
    if worker and worker.is_alive() and worker is not root.current_thread():
        worker.join(timeout=5)
    with root.SCAN_QUEUE_ACTIVATION_LOCK:
        root.SCAN_QUEUE_PROCESSING_ACTIVE = False

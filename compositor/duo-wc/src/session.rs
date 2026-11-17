//! The session the compositor starts.
//!
//! The systemd unit starts one command, and the compositor starts the session
//! itself: on the phone, a terminal per panel. It runs as a child, which is what
//! makes the lifetimes right in both directions - it cannot start before the
//! Wayland socket is listening, and it cannot outlive the display it is drawing on.

use std::os::fd::{AsRawFd as _, OwnedFd};
use std::os::unix::process::CommandExt as _;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use anyhow::{Context as _, Result};
use smithay::reexports::calloop::{channel, LoopHandle};
use tracing::{info, warn};

use crate::state::DuoWc;

/// A running session.
pub struct Session {
    /// Also the process group id: the child leads its own group.
    pid: i32,
    /// Set once the child has been reaped. After that the pid belongs to the
    /// kernel again and may already name someone else's process group, so the
    /// signal below must not go out.
    reaped: Arc<AtomicBool>,
}

impl Session {
    /// Ask the session and everything it started to end.
    ///
    /// The whole group, because the session is a shell script whose real work is
    /// its children - signalling only the script would leave the shell running on a
    /// display that is about to disappear.
    pub fn stop(&self) {
        if self.reaped.load(Ordering::SeqCst) {
            return;
        }
        // Safety: kill(2) with a negative pid signals the process group.
        unsafe { libc::kill(-self.pid, libc::SIGTERM) };
    }
}

/// The session must not outlive the compositor, whatever ends it.
///
/// It is deliberately in its own process group - so a signal aimed at the
/// compositor does not travel to it. Without this, an error path or a panic leaves
/// the clients running against a Wayland socket that no longer exists (systemd's
/// cgroup kill catches them on the phone, but not when started by hand).
impl Drop for Session {
    fn drop(&mut self) {
        self.stop();
    }
}

/// The descriptor the session finds its trust factory on (`DUO_WC_TRUST_FD`).
pub const TRUST_FD: i32 = 3;

/// What this duo-wc offers that the session may choose by
/// (`DUO_WC_FEATURES`, space-separated): `phosh-osk`, the globals the
/// on-screen keyboard phosh-osk-stub waits for (crate::devicestate). The
/// session starts it only when the compositor that started the session says
/// so: an older duo-wc (duo-wc.last-good put back after a failed update)
/// has not got them, and phosh-osk-stub would then wait for ever with no
/// keyboard at all, so the session starts squeekboard instead.
pub const FEATURES: &str = "phosh-osk";

/// Start the session, and stop the compositor when it exits.
///
/// The child gets its own process group so a signal meant for it does not travel
/// back to the compositor, and so the compositor can end the whole tree at once.
/// It gets the trust factory on fd 3 ([`crate::trust`]); duo-wc's own copy of
/// that end is closed once the child has it, and nothing else duo-wc starts
/// inherits it (it is close-on-exec everywhere but in this child).
pub fn spawn(
    loop_handle: &LoopHandle<'static, DuoWc>,
    command: Vec<String>,
    running: Arc<AtomicBool>,
    factory: OwnedFd,
) -> Result<Session> {
    let (program, arguments) = command.split_first().expect("parse rejects an empty command");
    let raw = factory.as_raw_fd();
    let mut command_line = Command::new(program);
    command_line
        .args(arguments)
        .env("DUO_WC_TRUST_FD", TRUST_FD.to_string())
        .env("DUO_WC_FEATURES", FEATURES)
        .process_group(0);
    // Safety: only async-signal-safe calls between fork and exec.
    unsafe {
        command_line.pre_exec(move || {
            if raw == TRUST_FD {
                if libc::fcntl(raw, libc::F_SETFD, 0) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
            } else if libc::dup2(raw, TRUST_FD) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child =
        command_line.spawn().with_context(|| format!("failed to start the session: {program}"))?;
    drop(factory);
    let pid = child.id() as i32;
    info!(pid, command = ?command, "session started");

    // The wait happens on a thread because the event loop must keep dispatching -
    // the session's first act is to connect to it. The channel is how the answer
    // gets back into the loop; a bare atomic would not wake it up.
    let (sender, receiver) = channel::channel::<i32>();
    let reaped = Arc::new(AtomicBool::new(false));
    let watcher_reaped = reaped.clone();
    std::thread::Builder::new()
        .name("session-wait".to_owned())
        .spawn(move || {
            let code = match child.wait() {
                Ok(status) => status.code().unwrap_or(-1),
                Err(_) => -1,
            };
            watcher_reaped.store(true, Ordering::SeqCst);
            let _ = sender.send(code);
        })
        .context("failed to start the session watcher")?;

    loop_handle
        .insert_source(receiver, move |event, _, _state| {
            if let channel::Event::Msg(code) = event {
                warn!(code, "the session exited - stopping");
                running.store(false, Ordering::SeqCst);
            }
        })
        .map_err(|err| anyhow::anyhow!("failed to watch the session: {err}"))?;

    Ok(Session { pid, reaped })
}

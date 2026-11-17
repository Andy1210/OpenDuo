//! The pen's buzz: datagrams to duo2-pend's socket (nibfair::haptic has the
//! rules). Never blocks and never fails the game: no socket (not this
//! phone, or duo2-pend without it), no permission, a full queue: the pulse
//! is dropped and the next attempt waits a few seconds.

use nibfair::haptic::{request_text, Limiter, Pulse};
use std::os::unix::net::UnixDatagram;

pub const SOCKET: &str = "/run/duo2-pen/haptics";

pub struct Buzz {
    sock: Option<UnixDatagram>,
    path: String,
    limiter: Limiter,
    retry_at: f64,
    logged: bool,
    pub enabled: bool,
    pub sent: u32,
}

impl Buzz {
    pub fn new() -> Buzz {
        let path = std::env::var("NIBFAIR_HAPTICS_SOCKET").unwrap_or_else(|_| SOCKET.to_owned());
        let enabled = std::env::var("NIBFAIR_HAPTICS")
            .map(|v| v != "0")
            .unwrap_or(true);
        Buzz {
            sock: None,
            path,
            limiter: Limiter::default(),
            retry_at: 0.0,
            logged: false,
            enabled,
            sent: 0,
        }
    }

    pub fn pulse(&mut self, now: f64, p: Pulse) {
        if !self.enabled {
            return;
        }
        if let Some(p) = self.limiter.offer(now, p) {
            self.send(now, &p);
        }
    }

    /// Every frame: a pulse that waited for its slot.
    pub fn poll(&mut self, now: f64) {
        if let Some(p) = self.limiter.poll(now) {
            self.send(now, &p);
        }
    }

    fn send(&mut self, now: f64, p: &Pulse) {
        if now < self.retry_at {
            return;
        }
        if self.sock.is_none() {
            match UnixDatagram::unbound() {
                Ok(s) => {
                    let _ = s.set_nonblocking(true);
                    self.sock = Some(s);
                }
                Err(_) => {
                    self.retry_at = now + 5.0;
                    return;
                }
            }
        }
        let msg = request_text(p);
        if let Some(s) = &self.sock {
            match s.send_to(msg.as_bytes(), &self.path) {
                Ok(_) => self.sent += 1,
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(e) => {
                    if !self.logged {
                        eprintln!(
                            "nibfair: no pen haptics ({}: {e}); trying again now and then",
                            self.path
                        );
                        self.logged = true;
                    }
                    self.retry_at = now + 3.0;
                }
            }
        }
    }
}

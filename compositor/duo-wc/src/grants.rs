//! One-time grants: how a root helper knows a request comes from the shell.
//!
//! Every app runs as the same user as the shell, and sudo cannot tell them
//! apart: any app could run `duo-shell-helper airplane on` or `reboot`. So
//! the helper's actions that matter (radios, mobile data, airplane mode,
//! reboot, power off, the touch daemon's restart) need a grant: the shell,
//! on its trusted connection, asks duo-wc for one (`grant`, privileged:
//! only the session's trusted clients and root have it), passes the token
//! to the helper on its standard input (never on a command line, which
//! every process can read), and the helper, as root, redeems it on the
//! admin socket (`redeem`). A grant names the exact action ("mobile-data
//! off"), lives [`TTL`] and is used once; an app that has no trusted
//! connection cannot get one.
//!
//! Pure: no Smithay types; the token's bytes come from the caller.

use std::time::{Duration, Instant};

/// How long a grant waits to be redeemed.
pub const TTL: Duration = Duration::from_secs(15);
/// At most this many waiting (a stuck shell cannot grow the list).
pub const MAX_PENDING: usize = 16;

/// The grants waiting to be redeemed.
#[derive(Debug, Default)]
pub struct Grants {
    pending: Vec<(String, String, Instant)>,
}

/// Whether an action is something a grant may name: a few words of
/// letters, digits and dashes (the helper's argument list).
pub fn valid_action(action: &str) -> bool {
    !action.is_empty()
        && action.len() <= 64
        && action
            .split(' ')
            .all(|w| !w.is_empty() && w.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
}

/// A token from random bytes, as hex.
pub fn token_of(bytes: &[u8; 16]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

impl Grants {
    /// A new grant for `action` with this token, from now.
    pub fn grant(&mut self, action: &str, token: String, now: Instant) -> Result<(), String> {
        if !valid_action(action) {
            return Err(format!("not an action: {action:?}"));
        }
        self.expire(now);
        if self.pending.len() >= MAX_PENDING {
            self.pending.remove(0);
        }
        self.pending.push((action.to_owned(), token, now + TTL));
        Ok(())
    }

    /// Redeem: true once, for the action the grant names, before it expires.
    pub fn redeem(&mut self, action: &str, token: &str, now: Instant) -> bool {
        self.expire(now);
        match self
            .pending
            .iter()
            .position(|(a, t, _)| a == action && !token.is_empty() && constant_eq(t, token))
        {
            Some(i) => {
                self.pending.remove(i);
                true
            }
            None => false,
        }
    }

    fn expire(&mut self, now: Instant) {
        self.pending.retain(|(_, _, until)| now < *until);
    }

    /// How many wait (tests, the state).
    pub fn len(&self) -> usize {
        self.pending.len()
    }

    /// Whether none waits.
    pub fn is_empty(&self) -> bool {
        self.pending.is_empty()
    }
}

/// Compare without stopping at the first difference.
fn constant_eq(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// 16 random bytes from the kernel.
pub fn random_token() -> std::io::Result<String> {
    use std::io::Read;
    let mut bytes = [0u8; 16];
    std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(token_of(&bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_grant_is_used_once_for_its_own_action() {
        let now = Instant::now();
        let mut g = Grants::default();
        g.grant("mobile-data off", "aa".repeat(16), now).unwrap();
        assert!(!g.redeem("mobile-data on", &"aa".repeat(16), now), "another action");
        assert!(!g.redeem("mobile-data off", &"ab".repeat(16), now), "another token");
        assert!(!g.redeem("mobile-data off", "", now));
        assert!(g.redeem("mobile-data off", &"aa".repeat(16), now));
        assert!(!g.redeem("mobile-data off", &"aa".repeat(16), now), "used once");
        assert!(g.is_empty());
    }

    #[test]
    fn a_grant_expires() {
        let now = Instant::now();
        let mut g = Grants::default();
        g.grant("reboot", "01".repeat(16), now).unwrap();
        assert!(!g.redeem("reboot", &"01".repeat(16), now + TTL));
        assert_eq!(g.len(), 0);
    }

    #[test]
    fn the_list_is_bounded_and_actions_are_checked() {
        let now = Instant::now();
        let mut g = Grants::default();
        for i in 0..(MAX_PENDING + 5) {
            g.grant("wifi on", format!("{i:032}"), now).unwrap();
        }
        assert_eq!(g.len(), MAX_PENDING);
        assert!(!g.redeem("wifi on", &format!("{:032}", 0), now), "the oldest went");
        assert!(g.redeem("wifi on", &format!("{:032}", MAX_PENDING + 4), now));
        for bad in ["", "a  b", "rm -rf /", "x;y", &"a".repeat(65)] {
            assert!(g.grant(bad, "t".into(), now).is_err(), "{bad:?}");
        }
        assert!(valid_action("airplane on") && valid_action("touch retry"));
    }

    #[test]
    fn tokens_are_hex() {
        assert_eq!(token_of(&[0xab; 16]), "ab".repeat(16));
        assert_eq!(random_token().unwrap().len(), 32);
    }
}

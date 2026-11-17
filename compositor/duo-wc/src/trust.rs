//! Who may ask duo-wc for what (docs/hardware/security.md).
//!
//! duo-wc runs as the GUI user, and so does every app: the user id says
//! nothing about whether a caller is the session (the shell, the lock, the
//! on-screen keyboard) or an app. So trust is not a user id but a
//! **capability**:
//!
//! - **The session**: duo-wc hands its session one end of a socketpair (the
//!   *trust factory*, fd 3, `DUO_WC_TRUST_FD`). Whoever holds it can get a
//!   trusted control connection or a trusted Wayland connection from it
//!   (`duoctl exec-trusted`, duo-shell's `trust.py`). Nothing else can reach
//!   it: it has no path, and an open socket cannot be reopened through
//!   `/proc/PID/fd`.
//! - **The administrator**: the admin socket (`admin.sock`, next to the
//!   user's) answers only root and the users in `admin_users` (duo2admin:
//!   the ssh tooling) with everything, and the users in `sensor_users`
//!   (duo-posed) with the posture requests. Checked by the peer's
//!   credentials (SO_PEERCRED), not by the file's permissions.
//! - **Everyone else** (any program of the GUI user, on the user's socket
//!   `duo-wc.sock`): the state, the event stream and the launch hint;
//!   nothing that unlocks, looks at the screen, injects input or moves
//!   other programs' windows.
//!
//! On Wayland the same split: a client that came through the factory (or the
//! admin socket) is *trusted* and sees the virtual keyboard, the input
//! method, the session lock, the foreign-toplevel lists, the layer shell,
//! the clipboard at any time (data control), the hardware-keyboard state and
//! the screen capture; a client on the public socket (`wayland-1`) does not
//! (text-input-v3 and the rest of the desktop protocols stay public).
//!
//! **Capture only** (the screen-cast portal, xdg-desktop-portal-wlr, and the
//! screenshot tool it runs): a narrower capability. A factory may hand out a
//! Wayland connection that sees the screen-capture globals and nothing else
//! of the guarded ones (`capture`), and a *capture factory* of its own
//! (`capture-factory`), which answers only those two asks. The portal holds
//! one: it can look at the screen (after the user picked a panel in its
//! chooser) but not type, lock, unlock or draw over everything.
//!
//! The pure part (roles, request classes, the protocol rule) is tested here;
//! the plumbing (peer credentials, passing descriptors) is in this module
//! too, the sockets in `crate::ipc`.

use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};

/// What a control connection may do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// Any program of the GUI user: read the state, the launch hint.
    App,
    /// duo-posed: the posture, pose, rotation and panel power, plus what an
    /// app may do.
    Sensors,
    /// The session (through the trust factory) or the administrator (root,
    /// `admin_users`): everything.
    Trusted,
    /// polkit (`confirm_users`): only `confirm`, the user's yes in the shell
    /// to an action (removing packages), nothing else.
    Confirmer,
}

impl Role {
    /// Whether this role may make a request of this class.
    pub fn permits(self, class: Class) -> bool {
        match class {
            Class::Read | Class::Hint => self != Role::Confirmer,
            Class::Sensors => matches!(self, Role::Sensors | Role::Trusted),
            Class::Confirm => matches!(self, Role::Confirmer | Role::Trusted),
            Class::Privileged => self == Role::Trusted,
        }
    }

    /// The name in log lines and errors.
    pub fn name(self) -> &'static str {
        match self {
            Role::App => "app",
            Role::Sensors => "sensors",
            Role::Trusted => "trusted",
            Role::Confirmer => "confirmer",
        }
    }
}

/// How sensitive a control request is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    /// Read-only state and the event stream.
    Read,
    /// Harmless hints (where the next new window opens).
    Hint,
    /// What the sensors drive: posture, pose, rotation, panel power.
    Sensors,
    /// Asking the user a question in the shell (`confirm`).
    Confirm,
    /// Everything else: unlock, screenshot, other programs' windows, the
    /// gesture and power settings, trusted sockets.
    Privileged,
}

/// Which socket a connection came in on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Door {
    /// `duo-wc.sock`, the GUI user's (mode 0600).
    User,
    /// `admin.sock`, for root, the administrator and duo-posed.
    Admin,
}

/// The role a peer gets on a door, or None (refused). `own_uid` is duo-wc's
/// (the GUI user's); `admins` and `sensors` are the uids of `admin_users`
/// and `sensor_users`.
pub fn role_for_peer(door: Door, uid: u32, own_uid: u32, admins: &[u32], sensors: &[u32]) -> Option<Role> {
    if uid == 0 {
        return Some(Role::Trusted);
    }
    match door {
        Door::User => (uid == own_uid).then_some(Role::App),
        Door::Admin => {
            // The GUI user's own programs never get more here than on their
            // own socket, even if the config names that user.
            if uid == own_uid {
                None
            } else if admins.contains(&uid) {
                Some(Role::Trusted)
            } else if sensors.contains(&uid) {
                Some(Role::Sensors)
            } else {
                None
            }
        }
    }
}

/// The role of a `confirm_users` peer on the admin socket (polkit asking
/// the user before PackageKit removes packages), when it got none else.
pub fn confirmer_role(door: Door, uid: u32, own_uid: u32, confirmers: &[u32]) -> Option<Role> {
    (door == Door::Admin && uid != 0 && uid != own_uid && confirmers.contains(&uid))
        .then_some(Role::Confirmer)
}

/// The Wayland globals that only trusted clients see.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Guarded {
    /// zwp_virtual_keyboard_manager_v1 (types into anything).
    VirtualKeyboard,
    /// zwp_input_method_manager_v2 (reads every text field's input).
    InputMethod,
    /// ext_session_lock_manager_v1 (locks, and by unlocking ends a lock).
    SessionLock,
    /// ext_foreign_toplevel_list_v1 and zwlr_foreign_toplevel_manager_v1
    /// (every window's title and app id; the second for the on-screen
    /// keyboard phosh-osk-stub, crate::wlrtoplevel).
    ForeignToplevels,
    /// zwlr_layer_shell_v1 (surfaces over everything: a fake lock screen).
    LayerShell,
    /// ext_data_control_manager_v1 and zwlr_data_control_manager_v1 (reads
    /// and sets the clipboard at any time, not only while focused: the
    /// Android clipboard bridge; the on-screen keyboard phosh-osk-stub's
    /// paste key).
    DataControl,
    /// zphoc_device_state_v1 (whether a hardware keyboard is attached: the
    /// on-screen keyboard phosh-osk-stub, crate::devicestate).
    DeviceState,
    /// zwlr_screencopy_manager_v1, ext_image_copy_capture_manager_v1 and
    /// ext_output_image_capture_source_manager_v1 (the screen's picture:
    /// the screen-cast portal, crate::capture).
    ScreenCapture,
}

/// What a Wayland client was given, beside the public globals.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Grant {
    /// The session's or the administrator's: every guarded global.
    pub trusted: bool,
    /// Capture only: the screen-capture globals (the portal).
    pub capture: bool,
}

/// Whether a client may see a guarded global.
pub fn may_bind(grant: Grant, global: Guarded) -> bool {
    match global {
        Guarded::ScreenCapture => grant.trusted || grant.capture,
        _ => grant.trusted,
    }
}

/// A user name as config accepts it (what useradd accepts, roughly).
pub fn valid_user_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 32
        && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.')
        && !name.starts_with('-')
}

/// The uid of a user name, from the password database (None: no such user).
pub fn uid_of(name: &str) -> Option<u32> {
    let cname = std::ffi::CString::new(name).ok()?;
    let mut pwd: libc::passwd = unsafe { std::mem::zeroed() };
    let mut result: *mut libc::passwd = std::ptr::null_mut();
    let found = with_growing_buffer(|buf| {
        result = std::ptr::null_mut();
        // Safety: getpwnam_r writes into pwd/buf, both live for the call.
        unsafe { libc::getpwnam_r(cname.as_ptr(), &mut pwd, buf.as_mut_ptr(), buf.len(), &mut result) }
    });
    (found && !result.is_null()).then_some(pwd.pw_uid)
}

/// The biggest buffer getpwnam_r is offered (a password entry is far smaller).
const PW_BUF_MAX: usize = 1 << 20;

/// Call a getpw*_r style function until it stops answering ERANGE ("the
/// buffer is too small"), doubling the buffer from 4 KiB up to `PW_BUF_MAX`.
/// True when it answered 0 (an answer, found or not: the result pointer says
/// which); any other error, or ERANGE at the limit, is false.
fn with_growing_buffer(mut call: impl FnMut(&mut [libc::c_char]) -> libc::c_int) -> bool {
    let mut buf = vec![0 as libc::c_char; 4096];
    loop {
        match call(&mut buf) {
            0 => return true,
            libc::ERANGE if buf.len() < PW_BUF_MAX => {
                let n = buf.len() * 2;
                buf.resize(n, 0);
            }
            _ => return false,
        }
    }
}

/// The uids of these user names (the ones that exist).
pub fn uids_of(names: &[String]) -> Vec<u32> {
    names.iter().filter_map(|n| uid_of(n)).collect()
}

/// The peer of a unix socket: (pid, uid, gid), as the kernel saw it at
/// connect time.
pub fn peer_cred(fd: &impl AsRawFd) -> std::io::Result<(i32, u32, u32)> {
    let mut cred = libc::ucred { pid: 0, uid: 0, gid: 0 };
    let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // Safety: cred and len are valid for getsockopt to write.
    let rc = unsafe {
        libc::getsockopt(
            fd.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            &mut cred as *mut libc::ucred as *mut libc::c_void,
            &mut len,
        )
    };
    if rc != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok((cred.pid, cred.uid, cred.gid))
}

/// A SOCK_SEQPACKET socketpair, both ends close-on-exec and the first
/// non-blocking: duo-wc's end and the end it hands out.
pub fn seqpacket_pair() -> std::io::Result<(OwnedFd, OwnedFd)> {
    let mut fds = [0 as RawFd; 2];
    // Safety: socketpair writes two descriptors into fds.
    let rc = unsafe {
        libc::socketpair(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0, fds.as_mut_ptr())
    };
    if rc != 0 {
        return Err(std::io::Error::last_os_error());
    }
    // Safety: both are fresh descriptors we own.
    let (ours, theirs) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
    set_nonblocking(&ours)?;
    Ok((ours, theirs))
}

fn set_nonblocking(fd: &impl AsRawFd) -> std::io::Result<()> {
    // Safety: plain fcntl on a descriptor we own.
    unsafe {
        let flags = libc::fcntl(fd.as_raw_fd(), libc::F_GETFL);
        if flags < 0 || libc::fcntl(fd.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(std::io::Error::last_os_error());
        }
    }
    Ok(())
}

/// Send `bytes` on a unix socket with one descriptor attached (SCM_RIGHTS).
/// The whole message goes in one sendmsg (a SEQPACKET packet, or a line on a
/// stream: the descriptor arrives with its first byte).
pub fn send_with_fd(socket: &impl AsRawFd, bytes: &[u8], fd: &impl AsRawFd) -> std::io::Result<()> {
    let mut iov = libc::iovec { iov_base: bytes.as_ptr() as *mut libc::c_void, iov_len: bytes.len() };
    // Room for one int, aligned (CMSG_SPACE is not const in libc).
    let space = unsafe { libc::CMSG_SPACE(std::mem::size_of::<RawFd>() as u32) } as usize;
    let mut control = vec![0u64; space.div_ceil(8)];
    let mut msg: libc::msghdr = unsafe { std::mem::zeroed() };
    msg.msg_iov = &mut iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control.as_mut_ptr() as *mut libc::c_void;
    msg.msg_controllen = space as _;
    // Safety: msg_control has CMSG_SPACE(sizeof int) bytes; the header and the
    // data are written inside it.
    unsafe {
        let cmsg = libc::CMSG_FIRSTHDR(&msg);
        (*cmsg).cmsg_level = libc::SOL_SOCKET;
        (*cmsg).cmsg_type = libc::SCM_RIGHTS;
        (*cmsg).cmsg_len = libc::CMSG_LEN(std::mem::size_of::<RawFd>() as u32) as _;
        std::ptr::write_unaligned(libc::CMSG_DATA(cmsg) as *mut RawFd, fd.as_raw_fd());
    }
    loop {
        // Safety: msg points at live buffers for the duration of the call.
        let n = unsafe { libc::sendmsg(socket.as_raw_fd(), &msg, libc::MSG_NOSIGNAL) };
        if n >= 0 {
            if (n as usize) < bytes.len() {
                return Err(std::io::Error::new(std::io::ErrorKind::WriteZero, "short sendmsg"));
            }
            return Ok(());
        }
        let err = std::io::Error::last_os_error();
        if err.kind() != std::io::ErrorKind::Interrupted {
            return Err(err);
        }
    }
}

/// Receive one SEQPACKET packet (no descriptors expected; any that come are
/// closed). Ok(None): nothing waiting; Ok(Some(empty)): the peer is gone.
pub fn recv_packet(socket: &impl AsRawFd) -> std::io::Result<Option<Vec<u8>>> {
    let mut buf = [0u8; 256];
    loop {
        // Safety: buf is valid for writes of its length. MSG_CMSG_CLOEXEC is
        // moot: recv takes no control data (the kernel drops passed fds).
        let n = unsafe {
            libc::recv(
                socket.as_raw_fd(),
                buf.as_mut_ptr() as *mut libc::c_void,
                buf.len(),
                libc::MSG_DONTWAIT,
            )
        };
        if n >= 0 {
            return Ok(Some(buf[..n as usize].to_vec()));
        }
        let err = std::io::Error::last_os_error();
        match err.kind() {
            std::io::ErrorKind::Interrupted => continue,
            std::io::ErrorKind::WouldBlock => return Ok(None),
            _ => return Err(err),
        }
    }
}

/// What a trust-factory packet asks for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FactoryAsk {
    /// A trusted Wayland connection (for WAYLAND_SOCKET).
    Wayland,
    /// A trusted control connection (the JSON lines protocol).
    Control,
    /// A Wayland connection that may capture the screen, nothing else
    /// guarded (the portal: [`Grant::capture`]).
    Capture,
    /// A factory of its own that answers only `capture` and
    /// `capture-factory` (for the portal's DUO_WC_TRUST_FD).
    CaptureFactory,
}

/// What a factory hands out.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FactoryLevel {
    /// The session's and the administrator's: everything.
    Full,
    /// The portal's: capture connections and capture factories only.
    Capture,
}

impl FactoryLevel {
    /// Whether a factory of this level answers this ask.
    pub fn permits(self, ask: FactoryAsk) -> bool {
        match self {
            FactoryLevel::Full => true,
            FactoryLevel::Capture => matches!(ask, FactoryAsk::Capture | FactoryAsk::CaptureFactory),
        }
    }
}

/// Parse a factory packet: `wayland`, `control`, `capture` or
/// `capture-factory` (a trailing newline is fine).
pub fn parse_factory_packet(packet: &[u8]) -> Option<FactoryAsk> {
    match std::str::from_utf8(packet).ok()?.trim() {
        "wayland" => Some(FactoryAsk::Wayland),
        "control" => Some(FactoryAsk::Control),
        "capture" => Some(FactoryAsk::Capture),
        "capture-factory" => Some(FactoryAsk::CaptureFactory),
        _ => None,
    }
}

/// Make this process non-dumpable: no other process of the same user may
/// ptrace it, read its memory or take its descriptors (pidfd_getfd), and its
/// /proc entries become root's. The kernel has no Yama (no ptrace_scope), so
/// without this any app of the GUI user could attach to duo-wc. Reset by
/// execve: children must do it themselves.
pub fn make_undumpable() -> std::io::Result<()> {
    // Safety: prctl(PR_SET_DUMPABLE, 0) takes no pointers.
    if unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const GUI_USER: u32 = 1000;
    const ADMIN: u32 = 1001;
    const POSED: u32 = 990;
    const OTHER: u32 = 1234;

    fn role(door: Door, uid: u32) -> Option<Role> {
        role_for_peer(door, uid, GUI_USER, &[ADMIN], &[POSED])
    }

    #[test]
    fn the_users_socket_gives_apps_the_app_role() {
        assert_eq!(role(Door::User, GUI_USER), Some(Role::App));
        assert_eq!(role(Door::User, 0), Some(Role::Trusted));
        // Nobody else should reach a 0600 socket; if they do, refused.
        assert_eq!(role(Door::User, ADMIN), None);
        assert_eq!(role(Door::User, OTHER), None);
    }

    #[test]
    fn the_admin_socket_answers_only_the_configured_users() {
        assert_eq!(role(Door::Admin, 0), Some(Role::Trusted));
        assert_eq!(role(Door::Admin, ADMIN), Some(Role::Trusted));
        assert_eq!(role(Door::Admin, POSED), Some(Role::Sensors));
        assert_eq!(role(Door::Admin, OTHER), None);
        // The GUI user's programs get nothing more on the admin socket.
        assert_eq!(role(Door::Admin, GUI_USER), None);
        assert_eq!(role_for_peer(Door::Admin, GUI_USER, GUI_USER, &[GUI_USER], &[GUI_USER]), None);
    }

    #[test]
    fn polkit_may_only_ask_the_user() {
        const POLKIT: u32 = 988;
        assert_eq!(confirmer_role(Door::Admin, POLKIT, GUI_USER, &[POLKIT]), Some(Role::Confirmer));
        assert_eq!(confirmer_role(Door::User, POLKIT, GUI_USER, &[POLKIT]), None);
        assert_eq!(confirmer_role(Door::Admin, GUI_USER, GUI_USER, &[GUI_USER]), None);
        assert_eq!(confirmer_role(Door::Admin, OTHER, GUI_USER, &[POLKIT]), None);
        assert!(Role::Confirmer.permits(Class::Confirm));
        assert!(Role::Trusted.permits(Class::Confirm));
        for class in [Class::Read, Class::Hint, Class::Sensors, Class::Privileged] {
            assert!(!Role::Confirmer.permits(class), "{class:?}");
        }
        assert!(!Role::App.permits(Class::Confirm));
        assert!(!Role::Sensors.permits(Class::Confirm));
    }

    #[test]
    fn roles_permit_by_class() {
        for class in [Class::Read, Class::Hint] {
            assert!(Role::App.permits(class));
            assert!(Role::Sensors.permits(class));
            assert!(Role::Trusted.permits(class));
        }
        assert!(!Role::App.permits(Class::Sensors));
        assert!(Role::Sensors.permits(Class::Sensors));
        assert!(Role::Trusted.permits(Class::Sensors));
        assert!(!Role::App.permits(Class::Privileged));
        assert!(!Role::Sensors.permits(Class::Privileged));
        assert!(Role::Trusted.permits(Class::Privileged));
    }

    const TRUSTED: Grant = Grant { trusted: true, capture: false };
    const CAPTURE: Grant = Grant { trusted: false, capture: true };
    const APP: Grant = Grant { trusted: false, capture: false };

    #[test]
    fn guarded_globals_only_for_trusted_clients() {
        for g in [
            Guarded::VirtualKeyboard,
            Guarded::InputMethod,
            Guarded::SessionLock,
            Guarded::ForeignToplevels,
            Guarded::LayerShell,
            Guarded::DataControl,
            Guarded::DeviceState,
            Guarded::ScreenCapture,
        ] {
            assert!(may_bind(TRUSTED, g));
            assert!(!may_bind(APP, g));
        }
    }

    #[test]
    fn the_portal_may_capture_and_nothing_else() {
        assert!(may_bind(CAPTURE, Guarded::ScreenCapture));
        for g in [
            Guarded::VirtualKeyboard,
            Guarded::InputMethod,
            Guarded::SessionLock,
            Guarded::ForeignToplevels,
            Guarded::LayerShell,
            Guarded::DataControl,
            Guarded::DeviceState,
        ] {
            assert!(!may_bind(CAPTURE, g), "{g:?}");
        }
    }

    #[test]
    fn factory_packets() {
        assert_eq!(parse_factory_packet(b"wayland"), Some(FactoryAsk::Wayland));
        assert_eq!(parse_factory_packet(b"control\n"), Some(FactoryAsk::Control));
        assert_eq!(parse_factory_packet(b"capture"), Some(FactoryAsk::Capture));
        assert_eq!(parse_factory_packet(b"capture-factory\n"), Some(FactoryAsk::CaptureFactory));
        assert_eq!(parse_factory_packet(b"factory"), None);
        assert_eq!(parse_factory_packet(b"\xff"), None);
        assert_eq!(parse_factory_packet(b""), None);
    }

    #[test]
    fn a_capture_factory_makes_only_capture_connections() {
        for ask in [FactoryAsk::Wayland, FactoryAsk::Control, FactoryAsk::Capture, FactoryAsk::CaptureFactory]
        {
            assert!(FactoryLevel::Full.permits(ask));
        }
        assert!(FactoryLevel::Capture.permits(FactoryAsk::Capture));
        assert!(FactoryLevel::Capture.permits(FactoryAsk::CaptureFactory));
        assert!(!FactoryLevel::Capture.permits(FactoryAsk::Wayland));
        assert!(!FactoryLevel::Capture.permits(FactoryAsk::Control));
    }

    #[test]
    fn user_names() {
        assert!(valid_user_name("duo2admin"));
        assert!(valid_user_name("duo-posed"));
        assert!(!valid_user_name(""));
        assert!(!valid_user_name("-x"));
        assert!(!valid_user_name("a b"));
        assert!(!valid_user_name("root;rm"));
    }

    #[test]
    fn root_resolves() {
        assert_eq!(uid_of("root"), Some(0));
        assert_eq!(uid_of("no-such-user-duo-wc-test"), None);
    }

    #[test]
    fn a_too_small_buffer_grows_instead_of_reading_as_absent() {
        let mut sizes = Vec::new();
        let ok = with_growing_buffer(|buf| {
            sizes.push(buf.len());
            if buf.len() < 20_000 {
                libc::ERANGE
            } else {
                0
            }
        });
        assert!(ok);
        assert_eq!(sizes, vec![4096, 8192, 16384, 32768]);
        // never past the limit, and other errors are final
        let mut last = 0;
        assert!(!with_growing_buffer(|buf| {
            last = buf.len();
            libc::ERANGE
        }));
        assert_eq!(last, PW_BUF_MAX);
        let mut calls = 0;
        assert!(!with_growing_buffer(|_| {
            calls += 1;
            libc::EINTR
        }));
        assert_eq!(calls, 1);
    }

    #[test]
    fn a_descriptor_travels_with_a_packet() {
        let (a, b) = seqpacket_pair().unwrap();
        let (payload, _keep) = seqpacket_pair().unwrap();
        send_with_fd(&b, b"ok", &payload).unwrap();
        // The packet arrives (recv drops the descriptor: fine for this test).
        assert_eq!(recv_packet(&a).unwrap().unwrap(), b"ok".to_vec());
        assert_eq!(recv_packet(&a).unwrap(), None);
        drop(b);
        assert_eq!(recv_packet(&a).unwrap().unwrap(), Vec::<u8>::new());
    }
}

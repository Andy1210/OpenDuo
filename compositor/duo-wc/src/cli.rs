//! The command line.
//!
//! Small on purpose: the compositor is started by a systemd unit with one shape of
//! arguments. The options override the config file.

use crate::layout::HingeGap;

/// How the compositor was asked to run.
#[derive(Debug, Default, PartialEq)]
pub struct Options {
    /// The config file to read instead of [`crate::config::DEFAULT_PATH`].
    pub config: Option<std::path::PathBuf>,
    /// The hinge gap to start with, over the config file's.
    pub hinge_gap: Option<HingeGap>,
    /// The output scale, over the config file's.
    pub scale: Option<f64>,
    /// The session to start once the compositor is up, if any.
    ///
    /// Everything after `--`. It inherits `WAYLAND_DISPLAY` and `DUO_WC_SOCKET`,
    /// and when it exits the compositor stops.
    pub session: Option<Vec<String>>,
}

/// What to do instead of running.
#[derive(Debug, PartialEq, Eq)]
pub enum Early {
    /// Print usage.
    Help,
    /// Print the version.
    Version,
}

/// Parse the arguments, without the program name.
pub fn parse<I: IntoIterator<Item = String>>(args: I) -> Result<(Options, Option<Early>), String> {
    let mut options = Options::default();
    let mut args = args.into_iter();

    while let Some(arg) = args.next() {
        let (flag, inline) = match arg.split_once('=') {
            Some((flag, value)) if flag.starts_with("--") => (flag.to_owned(), Some(value.to_owned())),
            _ => (arg.clone(), None),
        };
        let mut value = |name: &str| -> Result<String, String> {
            match inline.clone() {
                Some(value) => Ok(value),
                None => args.next().ok_or_else(|| format!("{name} needs a value")),
            }
        };
        match flag.as_str() {
            "--" => {
                let session: Vec<String> = args.collect();
                if session.is_empty() {
                    return Err("-- needs a command after it".to_owned());
                }
                options.session = Some(session);
                break;
            }
            "-h" | "--help" => return Ok((options, Some(Early::Help))),
            "-V" | "--version" => return Ok((options, Some(Early::Version))),
            "--config" => options.config = Some(value("--config")?.into()),
            "--hinge-gap" => {
                let word = value("--hinge-gap")?;
                options.hinge_gap = Some(
                    HingeGap::parse(&word).ok_or_else(|| format!("--hinge-gap is on or off, not {word}"))?,
                );
            }
            "--scale" => options.scale = Some(crate::config::parse_scale(&value("--scale")?)?),
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    Ok((options, None))
}

/// The usage text.
pub const USAGE: &str = "\
duo-wc - the Wayland compositor for the Surface Duo 2

Usage: duo-wc [OPTIONS] [-- COMMAND [ARGS...]]

  --config PATH        read PATH instead of /etc/duo-wc/duo-wc.conf
  --hinge-gap on|off   start with the hinge gap on (default) or off
  --scale N            output scale, 1..4, fractional allowed (default 2.5)
  -- COMMAND           start COMMAND once the compositor is up, stop when it exits
  -h, --help           print this
  -V, --version        print the version
";

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_args(args: &[&str]) -> Result<(Options, Option<Early>), String> {
        parse(args.iter().map(|a| (*a).to_owned()))
    }

    #[test]
    fn no_arguments_is_a_compositor_with_no_session() {
        assert_eq!(parse_args(&[]).unwrap(), (Options::default(), None));
    }

    #[test]
    fn everything_after_the_separator_is_the_session() {
        let (options, early) =
            parse_args(&["--hinge-gap", "off", "--", "/usr/lib/duo-wc/session", "--scale", "9"]).unwrap();
        assert_eq!(early, None);
        assert_eq!(options.hinge_gap, Some(HingeGap::Off));
        assert_eq!(options.scale, None);
        assert_eq!(
            options.session,
            Some(vec!["/usr/lib/duo-wc/session".to_owned(), "--scale".to_owned(), "9".to_owned()])
        );
    }

    #[test]
    fn values_inline_or_separate() {
        let (options, _) = parse_args(&["--scale=2.5", "--config", "/tmp/c"]).unwrap();
        assert_eq!(options.scale, Some(2.5));
        assert_eq!(options.config, Some("/tmp/c".into()));
    }

    #[test]
    fn mistakes_are_refused_rather_than_ignored() {
        assert!(parse_args(&["--"]).is_err());
        assert!(parse_args(&["--hinge-gap", "wide"]).is_err());
        assert!(parse_args(&["--scale"]).is_err());
        assert!(parse_args(&["--sesion", "x"]).is_err());
    }
}

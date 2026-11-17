use anyhow::Result;
use tracing_subscriber::EnvFilter;

fn main() -> Result<()> {
    let (options, early) = match duo_wc::cli::parse(std::env::args().skip(1)) {
        Ok(parsed) => parsed,
        Err(message) => {
            eprintln!("duo-wc: {message}\n\n{}", duo_wc::cli::USAGE);
            std::process::exit(2);
        }
    };
    match early {
        Some(duo_wc::cli::Early::Help) => {
            print!("{}", duo_wc::cli::USAGE);
            return Ok(());
        }
        Some(duo_wc::cli::Early::Version) => {
            println!("duo-wc {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        None => {}
    }

    // Under systemd stdout is the journal, which is where the log belongs: it
    // survives the compositor and reaches `journalctl -u duo-wc`. RUST_LOG picks
    // the level (`RUST_LOG=duo_wc=debug` logs every touch).
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt().with_env_filter(filter).with_ansi(false).init();

    // A panic goes to the journal with its backtrace, always: the default hook
    // gives one only with RUST_BACKTRACE set, and for a panic inside a Wayland
    // destructor (which aborts: the destructor is `extern "C"`) it printed
    // only the abort's own backtrace, without the frames that panicked
    // (2026-11-07, see teardown.rs).
    std::panic::set_hook(Box::new(|info| {
        let backtrace = std::backtrace::Backtrace::force_capture();
        tracing::error!("{info}\nbacktrace:\n{backtrace}");
    }));

    duo_wc::run(options)
}

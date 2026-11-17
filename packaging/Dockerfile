# The OpenDuo package builder (packaging/build-packages.sh, packaging/repo.sh,
# .github/workflows/release.yml): dpkg-deb, the repository tools, gpg. amd64:
# nothing here runs arm64 code (the built binaries come in staged). Pinned to a
# trixie point release so the archives' bytes do not move under a rebuild.
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 dpkg-dev xz-utils reprepro gnupg ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

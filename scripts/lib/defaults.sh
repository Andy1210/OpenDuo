# shellcheck shell=sh
# The project's defaults that more than one script needs (sourced by
# scripts/lib/remote.sh and scripts/build-images.sh; POSIX sh).
#
# The GUI user's name on a new image: duo-wc and every app run as this user
# (no sudo). It is the one place the name is chosen. A phone installed with
# another name keeps it: set DUO2_USER in local.env.
# shellcheck disable=SC2034 # read by the scripts that source this file
DUO2_USER_DEFAULT=duo

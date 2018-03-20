#!/usr/bin/env bash
# Copyright 2016 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

DIR="$( cd "$( dirname "$0" )" && pwd )"
source "$DIR"/_base.sh

function main {
    local GIT_TAG=${1:-"master"}

    install_pre_deps
    create_root
    install_deps
    install_gajim "$GIT_TAG"
    cleanup_install
    move_ssl_libs
    build_installer
}

main "$@";

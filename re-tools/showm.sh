#!/bin/bash
# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
# usage: showm.sh file classregex methodregex
awk -v C="$2" -v M="$3" '
/^(class|enum|interface) /{cls=$2; inm=0}
/^  method /{inm=(cls ~ C && $0 ~ M); if(inm) print "### " cls " :: " substr($0,10); next}
/^  field /{next}
inm && !/nop $/ {gsub(/\[\[[^]]*\]\]/,""); gsub(/System\.Runtime\.CompilerServices\./,""); print}
' "$1"

#!/usr/bin/env bash
# Source through BASH_ENV; persist locations only, never expanded commands/secrets.
if [[ -n ${MT5_SHELL_COVERAGE_DIR:-} ]]; then
	if [[ -n $(builtin trap -p DEBUG) && $(builtin trap -p DEBUG) != "trap -- '__mt5_shell_coverage_record' DEBUG" ]] ||
		[[ -n $(builtin trap -p RETURN) ]]; then
		printf 'Shell coverage cannot replace an existing DEBUG or RETURN trap\n' >&2
		exit 2
	fi
	if ! /bin/mkdir -p -- "$MT5_SHELL_COVERAGE_DIR"; then
		printf 'Cannot create shell coverage directory\n' >&2
		exit 2
	fi
	__mt5_shell_coverage_output=$(builtin cd -- "$MT5_SHELL_COVERAGE_DIR" && builtin pwd -P)
	# Invoked indirectly by the DEBUG trap and inherited by shell functions/subshells.
	# shellcheck disable=SC2317,SC2329
	__mt5_shell_coverage_record() {
		local frame source line
		for ((frame = 1; frame < ${#BASH_SOURCE[@]}; frame++)); do
			source=${BASH_SOURCE[frame]}
			line=${BASH_LINENO[frame - 1]:-0}
			[[ -n $source && $line != 0 ]] || continue
			if ! builtin printf '%s\0%s\0' "$source" "$line" >>"$__mt5_shell_coverage_output/bash-$BASHPID.trace"; then
				printf 'Cannot write shell coverage locations\n' >&2
				exit 2
			fi
		done
	}
	set -T
	builtin trap '__mt5_shell_coverage_record' DEBUG
fi

#!/usr/bin/env bash
# Each case must pass login, compilation, chart capture and a fresh expert test.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
: "${MT5_ENV_FILE:?Set MT5_ENV_FILE to your private account environment file}"
run_id=$(date -u +%Y%m%dT%H%M%SZ)
output=${OUTPUT_DIR:-$PWD/artifacts/matrix-$run_id}
mkdir -p "$output"
failed=0
container=''
volume=''
# --rm cleanup can continue after `docker stop` returns.
stop_container() {
	docker stop --timeout 15 "$container" >/dev/null 2>&1 || true
	local removal_deadline=$((SECONDS + 30))
	while docker inspect "$container" >/dev/null 2>&1; do
		if ((SECONDS >= removal_deadline)); then
			printf 'Container cleanup timed out: %s\n' "$container" >&2
			return 1
		fi
		sleep 0.1
	done
}
# Called indirectly by the EXIT trap; diagnostic differs across ShellCheck versions.
# shellcheck disable=SC2317,SC2329
cleanup() {
	if [[ -n $container ]]; then stop_container || true; fi
	if [[ -n $volume ]]; then docker volume rm "$volume" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
distros=("$@")
if [[ ${#distros[@]} == 0 ]]; then distros=(fedora-44 debian-13 ubuntu-26.04 archlinux); fi
for distro in "${distros[@]}"; do
	volume="mt5-check-$run_id-$distro"
	for backend in wayland x11; do
		for mode in headless nested vnc web; do
			case_name="$distro-$backend-$mode"
			container="mt5-check-$case_name"
			printf '\nValidating %s\n' "$case_name"
			if ! IMAGE_TAG="mt5:$distro" CONTAINER_NAME="$container" MT5_VOLUME="$volume" \
				DISPLAY_BACKEND="$backend" DISPLAY_MODE="$mode" \
				MT5_MCP_PORT=0 MT5_NATIVE_MCP_PORT=0 MT5_EDITOR_MCP_PORT=0 \
				VNC_PORT=0 WEB_PORT=0 VNC_PASSWORD=testpass \
				bin/mt5-run -d >"$output/$case_name-start.log" 2>&1; then
				failed=1
				continue
			fi
			deadline=$((SECONDS + 600))
			while [[ $(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || true) != healthy ]]; do
				if ((SECONDS >= deadline)) || ! docker inspect "$container" >/dev/null 2>&1; then break; fi
				sleep 3
			done
			port=$(docker port "$container" 8000/tcp | sed 's/.*://')
			validation_args=()
			if [[ $backend == wayland && ${KWIN_VARIANT:-source} == source && ( $distro == debian-13 || $distro == ubuntu-26.04 ) ]]; then
				validation_args+=(--kwin-version 6.8.2)
			fi
			if ! uv run --locked python tests/validate.py --container "$container" --case "$case_name" \
				--url "http://127.0.0.1:$port/mcp" "${validation_args[@]}" --output "$output" \
				>"$output/$case_name-validate.log" 2>&1; then failed=1; fi
			if [[ $mode == vnc || $mode == web ]]; then
				capture_args=()
				remote_port=5900
				if [[ $mode == web ]]; then capture_args+=(--web); remote_port=6080; fi
				port=$(docker port "$container" "$remote_port/tcp" | sed 's/.*://')
				mkdir -p "$output/$case_name"
				if ! uv run --locked python tests/capture-vnc.py --port "$port" --password testpass \
					"${capture_args[@]}" --output "$output/$case_name/remote.png" \
					>"$output/$case_name-remote.log" 2>&1; then failed=1; fi
			fi
			stop_container
			container=''
			printf 'Evidence: %s/%s\n' "$output" "$case_name"
		done
	done
	docker volume rm "$volume" >/dev/null
	volume=''
done
printf '\nAll evidence: %s\n' "$output"
exit "$failed"

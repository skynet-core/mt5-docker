#!/usr/bin/env bash
# Verify every compositor/display profile and retain its rendered/input evidence.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
distros=("$@")
if [[ ${#distros[@]} == 0 ]]; then distros=(fedora-44 debian-13 ubuntu-26.04 archlinux); fi
for distro in "${distros[@]}"; do
	case "$distro" in
	fedora-44 | debian-13 | ubuntu-26.04 | archlinux) ;;
	*) printf 'Unknown distribution: %s\n' "$distro" >&2; exit 2 ;;
	esac
done
if (( ${#distros[@]} > 1 )) && [[ -n ${IMAGE_TAG:-} || -n ${FIXTURE_IMAGE:-} ]]; then
	printf 'Select one distribution when overriding IMAGE_TAG or FIXTURE_IMAGE\n' >&2
	exit 2
fi
output=${OUTPUT_DIR:-$PWD/artifacts/display-matrix/$(date -u +%Y%m%dT%H%M%SZ)-$$}
mkdir -p "$output/_runner"
output=$(realpath -- "$output")
for distro in "${distros[@]}"; do
	if [[ -e $output/$distro ]]; then
		printf 'Evidence already exists: %s/%s; choose a new OUTPUT_DIR\n' "$output" "$distro" >&2
		exit 2
	fi
done
scratch=$(mktemp -d)
fixture_container=''
# Called indirectly by the EXIT trap; diagnostic differs across ShellCheck versions.
# shellcheck disable=SC2317,SC2329
cleanup() {
	if [[ -n $fixture_container ]]; then docker rm -fv "$fixture_container" >/dev/null 2>&1 || true; fi
	rm -rf -- "$scratch"
}
trap cleanup EXIT
failed=0
for distro in "${distros[@]}"; do
	image=${IMAGE_TAG:-mt5:$distro}
	fixture_image=${FIXTURE_IMAGE:-mt5:runtime-test-$distro}
	parent_image=${PARENT_IMAGE:-$image}
	fixture="$scratch/plasma-ui-fixture-$distro"
	log="$output/_runner/$distro.log"
	printf 'Testing %s: Wayland/X11 × headless/VNC/web/nested (both host transports)\n' "$distro"
	if fixture_container=$(docker create "$fixture_image" 2>>"$log") &&
		docker cp "$fixture_container:/usr/local/bin/plasma-ui-fixture" "$fixture" >>"$log" 2>&1; then
		docker rm -fv "$fixture_container" >>"$log" 2>&1
		fixture_container=''
		if ! uv run --locked python tests/runtime.py --image "$image" --parent-image "$parent_image" --fixture "$fixture" \
			--case "$distro" --capture-startup --output "$output" >>"$log" 2>&1; then
			failed=1
		fi
	else
		printf 'Could not export the matching fixture from %s\n' "$fixture_image" >>"$log"
		failed=1
		if [[ -n $fixture_container ]]; then docker rm -fv "$fixture_container" >>"$log" 2>&1 || true; fi
		fixture_container=''
	fi
	mkdir -p "$output/$distro"
	mv -- "$log" "$output/$distro/runner.log"
	printf 'Evidence: %s/%s\n' "$output" "$distro"
done
rmdir "$output/_runner" 2>/dev/null || true
if ! uv run --locked python tests/display_matrix.py "$output" --distros "${distros[@]}"; then failed=1; fi
printf 'Display matrix evidence: %s/INDEX.md\n' "$output"
exit "$failed"

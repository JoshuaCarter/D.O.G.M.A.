#!/usr/bin/env bash
# Copy live Anomaly user.ltx + GAMMA axr_options into repo appdata/.
# Sourced by pre-commit / pre-push. Skip: DOGMA_NO_APPDATA_SYNC=1
sync_appdata() {
	local root="${1:?repo root}"

	if [[ -n "${DOGMA_NO_APPDATA_SYNC:-}" ]]; then
		return 0
	fi

	local user_src axr_src dest_dir
	user_src="${DOGMA_USER_LTX:-/c/Anomaly/appdata/user.ltx}"
	axr_src="${DOGMA_AXR_OPTIONS:-/c/GAMMA/mods/G.A.M.M.A. MCM values - Rename to keep your personal changes/gamedata/configs/axr_options.ltx}"
	dest_dir="$root/appdata"

	local missing=0 src
	for src in "$user_src" "$axr_src"; do
		if [[ ! -f "$src" ]]; then
			echo "git hook: appdata source not found: $src" >&2
			missing=1
		fi
	done
	if [[ "$missing" == "1" ]]; then
		return 1
	fi

	mkdir -p "$dest_dir"
	cp -f "$user_src" "$dest_dir/user.ltx"
	cp -f "$axr_src" "$dest_dir/axr_options.ltx"
	git -C "$root" add appdata/user.ltx appdata/axr_options.ltx
	return 0
}

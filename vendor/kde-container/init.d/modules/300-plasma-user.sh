#!/usr/bin/bash
# Adjust only the desktop account; systemd must still start as container root.

module_plasma_user_enabled() {
	return 0
}

module_plasma_user_run() {
	[[ $(id -u) == 0 ]] || die 'Run PID 1 as root; use --user=0:0 with Podman keep-id'
	local password='' password_set=0 sudo_tag=''
	case ${PLASMA_SUDO_NOPASSWD-false} in
	true) sudo_tag='NOPASSWD: ' ;;
	false) ;;
	*) die 'PLASMA_SUDO_NOPASSWD must be true or false' ;;
	esac
	if [[ -v PLASMA_PASSWORD || -n ${PLASMA_PASSWORD_FILE:-} ]]; then
		password=${PLASMA_PASSWORD:-}
		if [[ -n ${PLASMA_PASSWORD_FILE:-} ]]; then
			[[ -f $PLASMA_PASSWORD_FILE && -r $PLASMA_PASSWORD_FILE ]] || die 'PLASMA_PASSWORD_FILE must be a readable regular file'
			password=$(cat -- "$PLASMA_PASSWORD_FILE")
		fi
		[[ -n $password && $password != *$'\n'* && $password != *$'\r'* ]] || die 'PLASMA_PASSWORD must be a nonempty single line'
		password_set=1
	fi
	local old_uid old_gid uid gid owner value
	old_uid=$(id -u plasma)
	old_gid=$(id -g plasma)
	uid=${PLASMA_UID:-$old_uid}
	gid=${PLASMA_GID:-$old_gid}
	for value in "$uid" "$gid"; do
		if [[ ! $value =~ ^[1-9][0-9]{0,9}$ ]] || ((value > 4294967294)); then
			die 'PLASMA_UID and PLASMA_GID must be nonzero numeric IDs below 4294967295'
		fi
	done
	owner=$(getent passwd "$uid" | cut -d: -f1) || true
	[[ -z $owner || $owner == plasma ]] || die "PLASMA_UID $uid already belongs to '$owner'"
	if [[ $gid != "$old_gid" ]] && ! getent group "$gid" >/dev/null; then
		groupmod --gid "$gid" "$(id -gn plasma)"
	fi
	if [[ $uid != "$old_uid" || $gid != "$old_gid" ]]; then
		# usermod also updates files owned by the old IDs inside the home.
		usermod --uid "$uid" --gid "$gid" plasma
		chown "$uid:$gid" /home/plasma
		if [[ $gid != "$old_gid" ]]; then
			find /home/plasma -xdev -gid "$old_gid" -exec chgrp --no-dereference "$gid" {} +
		fi
	fi
	install -d -m 0755 /etc/sudoers.d
	printf 'plasma ALL=(ALL) %sALL\n' "$sudo_tag" >/etc/sudoers.d/90-plasma
	chmod 0440 /etc/sudoers.d/90-plasma
	visudo --check --quiet || die 'Invalid sudoers configuration'
	if [[ $password_set == 1 ]]; then
		# chpasswd hashes plaintext; useradd/usermod --password expect a hash.
		printf 'plasma:%s\n' "$password" | chpasswd || die 'Could not set the plasma account password'
	fi
	unset PLASMA_PASSWORD
	info "Plasma identity: uid=$uid gid=$gid"
}

#!/usr/bin/bash

module_mt5_enabled() { return 0; }

module_mt5_run() {
	runuser -u plasma -- /usr/local/libexec/mt5-configure || die 'Cannot configure MetaTrader 5'
}


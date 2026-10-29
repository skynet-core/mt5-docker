// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace fake {
struct Request {
  std::string interface;
  uint32_t opcode = 0;
  std::vector<int64_t> values;
  std::vector<std::string> strings;
};
void reset(const std::string &mode, unsigned version = 6);
void keyboardEvents();
void keymap(const std::string &kind);
void capabilities(unsigned flags);
void global(const char *name, unsigned version);
void notify();
void offer(int mode, const std::map<std::string, std::string> &formats,
           bool known = true);
std::string request(int mode, const char *type, bool fail = false);
void cancel(int mode);
void finished();
const std::vector<Request> &requests();
} // namespace fake

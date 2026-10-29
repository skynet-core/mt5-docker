// SPDX-License-Identifier: GPL-2.0-or-later
// Include the real implementation so protocol/data guards are measured too.
#define main clipboard_application_main
#include "../../vendor/kde-container/native/clipboard.cpp"
#undef main
#include <QTemporaryFile>
#include <cassert>
#include <csignal>
#include <thread>

class RecordingEndpoint final : public Endpoint {
public:
  int selectedMode = -1;
  Data selected;
  void set(int mode, Data data) override {
    selectedMode = mode;
    selected = std::move(data);
  }
};

QByteArray published(RecordingEndpoint &endpoint, int mode, Data data) {
  FILE *capture = std::tmpfile();
  assert(capture);
  const int saved = dup(STDOUT_FILENO);
  assert(saved >= 0 && dup2(fileno(capture), STDOUT_FILENO) >= 0);
  endpoint.publish(mode, std::move(data));
  assert(dup2(saved, STDOUT_FILENO) >= 0);
  close(saved);
  std::rewind(capture);
  QByteArray output;
  std::array<char, 4096> bytes{};
  size_t size;
  while ((size = std::fread(bytes.data(), 1, bytes.size(), capture)))
    output.append(bytes.data(), static_cast<qsizetype>(size));
  std::fclose(capture);
  return output;
}

void core() {
  for (const auto *name :
       {"TARGETS", "MULTIPLE", "TIMESTAMP", "SAVE_TARGETS",
        "application/vnd.portal.filetransfer", "application/x-kde-source-id",
        "application/x-kde-onlyReplaceEmpty",
        "application/x-plasma6-clipboard-owner"})
    assert(sessionLocal(QString::fromLatin1(name)));
  assert(!sessionLocal(QStringLiteral("application/x-owned-binary")));
  const QStringList withPng{
      QStringLiteral("image/png"),     QStringLiteral("image/jpeg"),
      QStringLiteral("image/svg+xml"), QStringLiteral("application/x-qt-image"),
      QStringLiteral("text/plain"),    QStringLiteral("TIMESTAMP")};
  assert(transferFormats(withPng) ==
         QStringList({"image/png", "image/svg+xml", "text/plain"}));
  assert(transferFormats({"image/jpeg", "application/x-qt-image"}).size() == 2);
  Data plain{{"text/plain", "hello"}, {"TARGETS", "bad"}};
  normalize(plain);
  assert(plain.size() == 2 && plain.at("text/plain;charset=utf-8") == "hello");
  Data utf8{{"text/plain;charset=utf-8", "world"}};
  normalize(utf8);
  assert(utf8.at("text/plain") == "world");
  Data binary{{"application/x-binary", QByteArray("\0\1\2", 3)}};
  normalize(binary);
  assert(binary.size() == 1);
  QByteArray buffer;
  int messages = 0;
  auto receive = [&](const QJsonObject &object) {
    ++messages;
    assert(object.value("number").toInt() == messages);
  };
  const auto first = frame({{"number", 1}});
  consume(buffer, first.left(2), receive);
  assert(messages == 0 && buffer.size() == 2);
  consume(buffer, first.mid(2) + frame({{"number", 2}}) + "{", receive);
  assert(messages == 2 && buffer == "{");
  RecordingEndpoint endpoint;
  endpoint.apply(
      {{"mode", 1},
       {"data", QJsonObject{{"text/plain", "dGVzdA=="}, {"TARGETS", "eA=="}}}});
  assert(endpoint.selectedMode == 1 && endpoint.selected.size() == 2 &&
         endpoint.selected.at("text/plain") == "test");
  auto output = QJsonDocument::fromJson(
      published(endpoint, 0, {{"text/plain", "content"}}));
  assert(output.object().value("initial").toBool());
  assert(output.object().value("data").toObject().size() == 2);
  output = QJsonDocument::fromJson(published(endpoint, 0, {}));
  assert(!output.object().value("initial").toBool());
  assert(published(endpoint, 0,
                   {{"application/x-too-large", QByteArray(maxBytes + 1, 'x')}})
             .isEmpty());
}

void io() {
  QByteArray data("hello");
  assert(!transfer(-1, data, true));
  int descriptors[2];
  assert(pipe(descriptors) == 0);
  assert(transfer(descriptors[1], data, true));
  close(descriptors[1]);
  QByteArray received;
  assert(transfer(descriptors[0], received, false) && received == data);
  close(descriptors[0]);
  assert(pipe(descriptors) == 0);
  close(descriptors[0]);
  assert(!transfer(descriptors[1], data, true));
  close(descriptors[1]);
  assert(pipe(descriptors) == 0);
  QByteArray large(maxBytes + 262144, 'x');
  std::thread writer([&] {
    qsizetype offset = 0;
    while (offset < large.size()) {
      const auto count = write(descriptors[1], large.constData() + offset,
                               static_cast<size_t>(large.size() - offset));
      if (count <= 0)
        break;
      offset += count;
    }
    close(descriptors[1]);
  });
  QByteArray over;
  assert(!transfer(descriptors[0], over, false));
  close(descriptors[0]);
  writer.join();
}

int main(int argc, char **argv) {
  std::signal(SIGPIPE, SIG_IGN);
  assert(argc == 2);
  const QByteArray scenario(argv[1]);
  if (scenario == "core")
    core();
  else if (scenario == "transfer")
    io();
  else if (scenario == "timeout") {
    int descriptors[2];
    assert(pipe(descriptors) == 0);
    QByteArray empty;
    assert(!transfer(descriptors[0], empty, false));
    close(descriptors[0]);
    close(descriptors[1]);
  } else if (scenario == "invalid-json") {
    QByteArray buffer;
    consume(buffer, "[]\n", [](const QJsonObject &) {});
  } else if (scenario == "wire-limit") {
    QByteArray buffer(maxWireBytes, 'x');
    consume(buffer, "x", [](const QJsonObject &) {});
  } else if (scenario == "invalid-mode") {
    RecordingEndpoint endpoint;
    endpoint.apply({{"mode", 2}, {"data", QJsonObject{}}});
  } else if (scenario == "invalid-data") {
    RecordingEndpoint endpoint;
    endpoint.apply({{"mode", 0}, {"data", false}});
  } else if (scenario == "invalid-base64") {
    RecordingEndpoint endpoint;
    endpoint.apply({{"mode", 0}, {"data", QJsonObject{{"text/plain", "%%%"}}}});
  } else if (scenario == "data-limit") {
    RecordingEndpoint endpoint;
    endpoint.apply(
        {{"mode", 0},
         {"data",
          QJsonObject{
              {"text/plain", QString::fromLatin1(
                                 QByteArray(maxBytes + 1, 'x').toBase64())}}}});
  } else if (scenario == "stdout-failed") {
    const int full = open("/dev/full", O_WRONLY);
    assert(full >= 0 && dup2(full, STDOUT_FILENO) >= 0);
    close(full);
    RecordingEndpoint endpoint;
    endpoint.publish(0, {{"text/plain", "x"}});
  } else
    return 2;
  return 0;
}

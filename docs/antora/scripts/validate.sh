#!/usr/bin/env bash
# Validate the standalone SAW Antora component before publishing.
set -euo pipefail

DOCS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENT="${DOCS_DIR}/antora.yml"
ROOT="${DOCS_DIR}/modules/ROOT"

fail() {
  printf 'SAW documentation validation error: %s\n' "$1" >&2
  exit 1
}

[[ -f "${COMPONENT}" ]] || fail "missing ${COMPONENT}"
[[ -f "${ROOT}/nav.adoc" ]] || fail "missing nav.adoc"

for page in "${ROOT}"/pages/*.adoc; do
  [[ -f "${page}" ]] || fail "missing page ${page}"
done

start_page="$(ruby -e 'require "yaml"; puts YAML.load_file(ARGV[0]).fetch("start_page")' "${COMPONENT}")"
[[ "${start_page}" == ROOT:* ]] || fail "start_page must stay within the ROOT module"
relative_start_page="${start_page#ROOT:}"
[[ -f "${ROOT}/pages/${relative_start_page}" ]] || fail "start_page target ${relative_start_page} is missing"

ruby -e '
  require "yaml"
  component = ARGV[0]
  root = ARGV[1]
  attrs = YAML.load_file(component).dig("asciidoc", "attributes").keys.map(&:to_s)
  errors = []
  Dir[File.join(root, "**", "*.adoc")].each do |file|
    content = File.read(file)
    content.scan(/(?<![$\\])\{([A-Za-z][A-Za-z0-9_]*)\}/).flatten.uniq.each do |attr|
      errors << "undefined attribute #{attr} in #{file}" unless attrs.include?(attr)
    end
    content.scan(/include::partial\$([^\[]+)\[\]/).flatten.each do |partial|
      errors << "missing partial #{partial} referenced by #{file}" unless File.file?(File.join(root, "partials", partial))
    end
    content.scan(/xref:([^:\[#]+\.adoc)\[/).flatten.each do |target|
      errors << "missing page #{target} referenced by #{file}" unless File.file?(File.join(root, "pages", target))
    end
  end
  abort errors.join("\n") unless errors.empty?
' "${COMPONENT}" "${ROOT}"

ruby -e '
  require "yaml"
  require "open3"
  attrs = YAML.load_file(ARGV[0]).dig("asciidoc", "attributes").transform_keys(&:to_s)
  root = ARGV[1]
  errors = []
  Dir[File.join(root, "**", "*.adoc")].each do |file|
    lines = File.readlines(file)
    i = 0
    while i < lines.length
      next i += 1 unless lines[i].start_with?("[source,bash")
      next i += 1 unless lines[i + 1]&.strip == "----"
      j = i + 2
      j += 1 while j < lines.length && lines[j].strip != "----"
      block = lines[(i + 2)...j].join
      attrs.each { |k, v| block.gsub!("{#{k}}", v.to_s) }
      block.gsub!("\\{", "{")
      _, err, status = Open3.capture3("bash", "-n", stdin_data: block)
      errors << "#{file}:#{i + 3}: #{err.strip}" unless status.success?
      i = j
    end
  end
  abort errors.join("\n") unless errors.empty?
' "${COMPONENT}" "${ROOT}"

printf 'SAW documentation validation passed.\n'

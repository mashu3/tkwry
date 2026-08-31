//! Detect tkwry RPC envelopes without substring false positives.

use std::borrow::Cow;

/// True when *body* is a JSON object whose ``__tkwry`` field is the string ``rpc``.
///
/// Used only to pick the dedicated RPC queue so IPC overflow cannot drop
/// in-flight ``window.tkwry.call`` requests. Python still parses the envelope.
pub fn is_rpc_envelope(body: &str) -> bool {
    matches!(
        json_object_get_string(body.trim_start(), "__tkwry").as_deref(),
        Some("rpc")
    )
}

fn json_object_get_string<'a>(body: &'a str, want_key: &str) -> Option<Cow<'a, str>> {
    let mut rest = skip_ws(body);
    rest = rest.strip_prefix('{')?;
    loop {
        rest = skip_ws(rest);
        if rest.starts_with('}') {
            return None;
        }
        let (key, after_key) = parse_json_string(rest)?;
        if key != want_key {
            rest = skip_ws(after_key);
            rest = rest.strip_prefix(':')?;
            rest = skip_json_value(skip_ws(rest))?;
        } else {
            rest = skip_ws(after_key);
            rest = rest.strip_prefix(':')?;
            return parse_json_string(skip_ws(rest)).map(|(value, _)| value);
        }
        rest = skip_ws(rest);
        if rest.starts_with(',') {
            rest = skip_ws(&rest[1..]);
            continue;
        }
        if rest.starts_with('}') {
            return None;
        }
        return None;
    }
}

fn skip_ws(s: &str) -> &str {
    s.trim_start()
}

fn parse_json_string(s: &str) -> Option<(Cow<'_, str>, &str)> {
    let s = skip_ws(s);
    let rest = s.strip_prefix('"')?;
    let mut out = String::new();
    let mut chars = rest.chars();
    loop {
        match chars.next()? {
            '"' => {
                let consumed = s.len() - chars.as_str().len();
                let cow = if out.is_empty() {
                    Cow::Borrowed(&s[1..consumed - 1])
                } else {
                    Cow::Owned(out)
                };
                return Some((cow, chars.as_str()));
            }
            '\\' => {
                let ch = chars.next()?;
                match ch {
                    '"' | '\\' | '/' => out.push(ch),
                    'b' => out.push('\x08'),
                    'f' => out.push('\x0c'),
                    'n' => out.push('\n'),
                    'r' => out.push('\r'),
                    't' => out.push('\t'),
                    'u' => {
                        let hex: String = chars.by_ref().take(4).collect();
                        if hex.len() != 4 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
                            return None;
                        }
                        let code = u32::from_str_radix(&hex, 16).ok()?;
                        out.push(char::from_u32(code)?);
                    }
                    _ => return None,
                }
            }
            ch if ch.is_control() => return None,
            ch => out.push(ch),
        }
    }
}

fn skip_json_value(s: &str) -> Option<&str> {
    let s = skip_ws(s);
    if s.starts_with('"') {
        let (_, rest) = parse_json_string(s)?;
        return Some(rest);
    }
    if let Some(rest) = s.strip_prefix('{') {
        return skip_json_object(rest);
    }
    if let Some(rest) = s.strip_prefix('[') {
        return skip_json_array(rest);
    }
    if let Some(rest) = s.strip_prefix("true") {
        return Some(rest);
    }
    if let Some(rest) = s.strip_prefix("false") {
        return Some(rest);
    }
    if let Some(rest) = s.strip_prefix("null") {
        return Some(rest);
    }
    skip_json_number(s)
}

fn skip_json_object(s: &str) -> Option<&str> {
    let mut rest = skip_ws(s);
    if let Some(tail) = rest.strip_prefix('}') {
        return Some(tail);
    }
    loop {
        let (_, after_key) = parse_json_string(rest)?;
        rest = skip_ws(after_key);
        rest = rest.strip_prefix(':')?;
        rest = skip_json_value(skip_ws(rest))?;
        rest = skip_ws(rest);
        if rest.starts_with(',') {
            rest = skip_ws(&rest[1..]);
            continue;
        }
        return rest.strip_prefix('}').map(skip_ws);
    }
}

fn skip_json_array(s: &str) -> Option<&str> {
    let mut rest = skip_ws(s);
    if let Some(tail) = rest.strip_prefix(']') {
        return Some(tail);
    }
    loop {
        rest = skip_json_value(rest)?;
        rest = skip_ws(rest);
        if rest.starts_with(',') {
            rest = skip_ws(&rest[1..]);
            continue;
        }
        return rest.strip_prefix(']').map(skip_ws);
    }
}

fn skip_json_number(s: &str) -> Option<&str> {
    let mut idx = 0;
    let bytes = s.as_bytes();
    if bytes.first() == Some(&b'-') {
        idx += 1;
    }
    while idx < bytes.len() && bytes[idx].is_ascii_digit() {
        idx += 1;
    }
    if idx < bytes.len() && bytes[idx] == b'.' {
        idx += 1;
        while idx < bytes.len() && bytes[idx].is_ascii_digit() {
            idx += 1;
        }
    }
    if idx < bytes.len() && (bytes[idx] == b'e' || bytes[idx] == b'E') {
        idx += 1;
        if idx < bytes.len() && (bytes[idx] == b'+' || bytes[idx] == b'-') {
            idx += 1;
        }
        while idx < bytes.len() && bytes[idx].is_ascii_digit() {
            idx += 1;
        }
    }
    if idx == 0 || (bytes[0] == b'-' && idx == 1) {
        return None;
    }
    Some(&s[idx..])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_compact_and_spaced_json() {
        assert!(is_rpc_envelope(
            r#"{"__tkwry":"rpc","id":"r1","method":"ping","params":[]}"#
        ));
        assert!(is_rpc_envelope(
            r#"{ "__tkwry": "rpc", "id": "r1", "method": "ping" }"#
        ));
    }

    #[test]
    fn rejects_non_rpc_and_invalid_json() {
        assert!(!is_rpc_envelope(r#"{"action":"increment"}"#));
        assert!(!is_rpc_envelope("not-json"));
        assert!(!is_rpc_envelope(r#"{"__tkwry":"event"}"#));
    }

    #[test]
    fn rejects_marker_inside_ipc_string_value() {
        assert!(!is_rpc_envelope(
            r#"{"note":"literal \"__tkwry\":\"rpc\" inside"}"#
        ));
        assert!(!is_rpc_envelope(
            r#"{"payload":"{\"__tkwry\":\"rpc\"} is not the root"}"#
        ));
    }
}

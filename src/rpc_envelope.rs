//! Detect tkwry RPC envelopes without substring false positives.

use std::borrow::Cow;

/// Maximum nesting depth when skipping JSON values for envelope detection.
///
/// Deeper payloads are treated as non-RPC so a hostile IPC message cannot blow
/// the Rust stack.
const MAX_JSON_SKIP_DEPTH: usize = 128;

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

/// Context for the iterative JSON value skipper.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SkipCtx {
    /// Inside `{` — need key or `}`.
    ObjectKey,
    /// After key — need `:` then value.
    ObjectColon,
    /// After value — need `,` or `}`.
    ObjectComma,
    /// Inside `[` — need value or `]`.
    ArrayValue,
    /// After value — need `,` or `]`.
    ArrayComma,
}

fn skip_json_value(s: &str) -> Option<&str> {
    let mut rest = s;
    let mut stack: Vec<SkipCtx> = Vec::new();

    loop {
        rest = skip_ws(rest);
        if stack.is_empty() {
            rest = skip_json_atom(rest, &mut stack)?;
            if stack.is_empty() {
                return Some(rest);
            }
            continue;
        }

        match *stack.last().unwrap() {
            SkipCtx::ObjectKey => {
                if rest.starts_with('}') {
                    rest = skip_ws(rest.strip_prefix('}')?);
                    stack.pop();
                    continue;
                }
                let (_, after_key) = parse_json_string(rest)?;
                rest = skip_ws(after_key);
                rest = rest.strip_prefix(':')?;
                if let Some(slot) = stack.last_mut() {
                    *slot = SkipCtx::ObjectColon;
                }
            }
            SkipCtx::ObjectColon => {
                rest = skip_json_atom(rest, &mut stack)?;
                if let Some(slot) = stack.last_mut() {
                    *slot = SkipCtx::ObjectComma;
                }
            }
            SkipCtx::ObjectComma => {
                if rest.starts_with(',') {
                    rest = skip_ws(&rest[1..]);
                    if let Some(slot) = stack.last_mut() {
                        *slot = SkipCtx::ObjectKey;
                    }
                } else if rest.starts_with('}') {
                    rest = skip_ws(rest.strip_prefix('}')?);
                    stack.pop();
                } else {
                    return None;
                }
            }
            SkipCtx::ArrayValue => {
                if rest.starts_with(']') {
                    rest = skip_ws(rest.strip_prefix(']')?);
                    stack.pop();
                    continue;
                }
                rest = skip_json_atom(rest, &mut stack)?;
                if let Some(slot) = stack.last_mut() {
                    *slot = SkipCtx::ArrayComma;
                }
            }
            SkipCtx::ArrayComma => {
                if rest.starts_with(',') {
                    rest = skip_ws(&rest[1..]);
                    if let Some(slot) = stack.last_mut() {
                        *slot = SkipCtx::ArrayValue;
                    }
                } else if rest.starts_with(']') {
                    rest = skip_ws(rest.strip_prefix(']')?);
                    stack.pop();
                } else {
                    return None;
                }
            }
        }
    }
}

/// Skip one JSON value atom (scalar or container opener).
fn skip_json_atom<'a>(s: &'a str, stack: &mut Vec<SkipCtx>) -> Option<&'a str> {
    let s = skip_ws(s);
    if s.starts_with('"') {
        let (_, rest) = parse_json_string(s)?;
        return Some(rest);
    }
    if let Some(rest) = s.strip_prefix('{') {
        if stack.len() >= MAX_JSON_SKIP_DEPTH {
            return None;
        }
        let rest = skip_ws(rest);
        if rest.starts_with('}') {
            return rest.strip_prefix('}').map(skip_ws);
        }
        stack.push(SkipCtx::ObjectKey);
        return Some(rest);
    }
    if let Some(rest) = s.strip_prefix('[') {
        if stack.len() >= MAX_JSON_SKIP_DEPTH {
            return None;
        }
        let rest = skip_ws(rest);
        if rest.starts_with(']') {
            return rest.strip_prefix(']').map(skip_ws);
        }
        stack.push(SkipCtx::ArrayValue);
        return Some(rest);
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

    #[test]
    fn deep_nesting_does_not_stack_overflow() {
        let depth = 10_000;
        let nested = format!("{}{}", "[".repeat(depth), "]".repeat(depth));
        assert!(!is_rpc_envelope(&nested));
        let object_nested = format!("{}{}", "{".repeat(depth), "}".repeat(depth));
        assert!(!is_rpc_envelope(&object_nested));
    }

    #[test]
    fn depth_at_cap_still_skips_nested_rpc_marker() {
        let mut inner = String::from(r#""__tkwry":"rpc""#);
        for _ in 0..MAX_JSON_SKIP_DEPTH {
            inner = format!("{{{inner}}}");
        }
        assert!(!is_rpc_envelope(&inner));
    }
}

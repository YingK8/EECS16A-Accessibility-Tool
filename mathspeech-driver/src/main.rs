//! MathML -> speech, one long-lived process for the whole batch.
//!
//! This is the same shape `speech.cjs` had, and for the same reason: MathCAT's
//! own CLI takes one expression per invocation, which at 35,504 unique formulas
//! is 35,504 process spawns. Loading the rules once costs seconds; loading them
//! per formula costs hours.
//!
//! Protocol is JSON Lines both ways -- `{"hash","mathml"}` in, `{"hash","speech"}`
//! or `{"hash","error"}` out -- unchanged from the SRE driver, so the Python
//! side only had to change which program it spawns. One formula MathCAT chokes
//! on cannot take down the batch.
//!
//! MathCAT keeps its engine in thread-local state, so this stays single
//! threaded on purpose. The batch is I/O-shaped anyway: the caller feeds the
//! whole payload in one write.

use std::io::{self, Read, Write};

use libmathcat::interface::{errors_to_string, get_spoken_text, set_mathml, set_preference, set_rules_dir};
use serde_json::{json, Value};

/// Preferences that decide how this corpus is read aloud.
///
/// `TTS=None` is the load-bearing one. With a TTS engine selected MathCAT
/// interleaves pause and rate commands into the string; that string becomes a
/// PDF `/Alt`, where a screen reader would read the markup out as words.
///
/// `ClearSpeak_Matrix=Auto` and `ClearSpeak_MultiLineLabel=Equation` are
/// the two that were the reason for moving off SRE. This is a linear-algebra
/// course: 357 of its matrices are `array`-in-matrix and 257 of those carry a
/// `|` column divider, and `align`/`align*` outnumber `equation` about four to
/// one. Flat ClearSpeak says "the 2 by 3 matrix" and stops.
const PREFERENCES: &[(&str, &str)] = &[
    ("Language", "en"),
    ("SpeechStyle", "ClearSpeak"),
    ("TTS", "None"),
    ("Verbosity", "Medium"),
    ("Impairment", "Blindness"),
    ("Bookmark", "false"),
    ("DecimalSeparator", "Auto"),
    ("ClearSpeak_Matrix", "Auto"),
    ("ClearSpeak_MultiLineLabel", "Equation"),
];

fn speak(mathml: &str) -> Result<String, String> {
    set_mathml(mathml.to_string()).map_err(|e| errors_to_string(&e))?;
    get_spoken_text().map_err(|e| errors_to_string(&e))
}

fn main() {
    let mut args = std::env::args().skip(1);
    let rules_dir = args.next().unwrap_or_default();
    // The remaining argument is the speech style, so `domain` in the old driver
    // keeps meaning what it meant at the Python call site.
    let style = args.next();

    if let Err(e) = set_rules_dir(rules_dir) {
        eprintln!("MathCAT could not load its rules: {}", errors_to_string(&e));
        std::process::exit(2);
    }
    for (name, value) in PREFERENCES {
        if let Err(e) = set_preference(name.to_string(), value.to_string()) {
            eprintln!("MathCAT rejected {name}={value}: {}", errors_to_string(&e));
            std::process::exit(2);
        }
    }
    if let Some(style) = style {
        if let Err(e) = set_preference("SpeechStyle".to_string(), style.clone()) {
            eprintln!("MathCAT rejected SpeechStyle={style}: {}", errors_to_string(&e));
            std::process::exit(2);
        }
    }

    let mut payload = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut payload) {
        eprintln!("could not read the batch: {e}");
        std::process::exit(2);
    }

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in payload.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let record: Value = match serde_json::from_str(line) {
            Ok(value) => value,
            // A malformed line has no hash to report against, so there is
            // nowhere to send a per-formula error. Say so and keep going.
            Err(e) => {
                eprintln!("skipping unparseable input line: {e}");
                continue;
            }
        };
        let hash = record["hash"].as_str().unwrap_or_default();
        let mathml = record["mathml"].as_str().unwrap_or_default();
        let reply = match speak(mathml) {
            Ok(speech) => json!({ "hash": hash, "speech": speech }),
            Err(error) => json!({ "hash": hash, "error": error }),
        };
        if writeln!(out, "{reply}").is_err() {
            std::process::exit(2); // the caller went away
        }
    }
    let _ = out.flush();
}

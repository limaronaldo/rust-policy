//! Port parsing for the sample workspace.
#![forbid(unsafe_code)]

/// Parses a decimal port number.
///
/// # Errors
///
/// Returns an error for invalid digits, an empty input, or a value above 65535.
///
/// # Examples
///
/// ```
/// # fn main() -> Result<(), std::num::ParseIntError> {
/// assert_eq!(sample_core::parse_port("8080")?, 8080);
/// # Ok(())
/// # }
/// ```
pub fn parse_port(input: &str) -> Result<u16, std::num::ParseIntError> {
    input.parse()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_out_of_range() {
        assert!(parse_port("70000").is_err());
    }
}

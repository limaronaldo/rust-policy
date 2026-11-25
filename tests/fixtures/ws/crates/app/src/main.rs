//! Sample binary.
#![forbid(unsafe_code)]

fn main() {
    let port = sample_core::parse_port("8080").expect("literal port is valid");
    let mut buf = itoa::Buffer::new();
    println!("port {}", buf.format(port));
}

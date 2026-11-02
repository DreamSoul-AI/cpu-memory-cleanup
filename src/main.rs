fn main() {
    std::process::exit(cpu_memory_cleanup::interaction::cli::main(
        std::env::args().skip(1).collect(),
    ));
}

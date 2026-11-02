use std::{env, fs, path::Path};

fn sources(path: &Path, files: &mut Vec<std::path::PathBuf>) {
    if path.is_dir() {
        for entry in fs::read_dir(path).expect("read source directory") {
            sources(&entry.expect("source entry").path(), files);
        }
    } else {
        files.push(path.to_path_buf());
    }
}

fn main() {
    let root = env::var("CARGO_MANIFEST_DIR").expect("manifest directory");
    let root = Path::new(&root);
    let mut files = Vec::new();
    sources(&root.join("src"), &mut files);
    for file in ["build.rs", "Cargo.toml", "Cargo.lock"] {
        files.push(root.join(file));
    }
    files.sort();
    // Stable source identity, not a cryptographic integrity or signature check.
    let mut hash = 0xcbf29ce484222325u64;
    println!("cargo:rerun-if-changed=src");
    for file in files {
        let relative = file.strip_prefix(root).expect("relative source");
        println!("cargo:rerun-if-changed={}", relative.display());
        let name = relative.to_string_lossy().replace('\\', "/");
        for byte in name
            .bytes()
            .chain([0])
            .chain(fs::read(&file).expect("source bytes"))
        {
            hash = (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3);
        }
    }
    println!("cargo:rustc-env=MEMORY_CLEANUP_BUILD_ID={hash:016x}");
}

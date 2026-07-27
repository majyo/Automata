#[cfg(not(windows))]
fn main() {
    eprintln!("automata-sandbox-host is only supported on Windows");
    std::process::exit(125);
}

#[cfg(windows)]
mod windows_host {
    use anyhow::{bail, Context, Result};
    use serde::Deserialize;
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::collections::BTreeMap;
    use std::ffi::{c_void, OsStr};
    use std::fs;
    use std::mem::{size_of, zeroed};
    use std::os::windows::ffi::OsStrExt;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT,
        INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Security::{FreeSid, SECURITY_CAPABILITIES};
    use windows_sys::Win32::System::Console::{
        GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
    };
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
        InitializeProcThreadAttributeList, ResumeThread, UpdateProcThreadAttribute,
        WaitForSingleObject, CREATE_NEW_PROCESS_GROUP, CREATE_SUSPENDED,
        CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT, INFINITE, PROCESS_INFORMATION,
        STARTF_USESTDHANDLES, STARTUPINFOEXW,
    };

    const REQUEST_SCHEMA_VERSION: u32 = 1;
    const PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES: usize = 0x0002_0009;

    #[link(name = "userenv")]
    extern "system" {
        fn CreateAppContainerProfile(
            app_container_name: *const u16,
            display_name: *const u16,
            description: *const u16,
            capabilities: *const c_void,
            capability_count: u32,
            app_container_sid: *mut *mut c_void,
        ) -> i32;
        fn DeriveAppContainerSidFromAppContainerName(
            app_container_name: *const u16,
            app_container_sid: *mut *mut c_void,
        ) -> i32;
    }

    #[derive(Debug, Deserialize)]
    struct Request {
        schema_version: u32,
        argv: Vec<String>,
        cwd: String,
        env: BTreeMap<String, String>,
        profile: PermissionProfile,
    }

    #[derive(Debug, Deserialize)]
    struct PermissionProfile {
        sandbox_enforcement: String,
        network: String,
        workspace_roots: Vec<String>,
        temporary_roots: Vec<String>,
        #[serde(default)]
        runtime_roots: Vec<String>,
        protected_paths: Vec<String>,
        deny_read_paths: Vec<String>,
        profile_hash: String,
    }

    struct OwnedHandle(HANDLE);

    impl OwnedHandle {
        fn new(handle: HANDLE, label: &str) -> Result<Self> {
            if handle == 0 || handle == INVALID_HANDLE_VALUE {
                bail!("{label} failed with Win32 error {}", unsafe {
                    GetLastError()
                });
            }
            Ok(Self(handle))
        }
    }

    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            if self.0 != 0 && self.0 != INVALID_HANDLE_VALUE {
                unsafe {
                    CloseHandle(self.0);
                }
            }
        }
    }

    struct OwnedSid(*mut c_void);

    impl Drop for OwnedSid {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    FreeSid(self.0);
                }
            }
        }
    }

    struct AttributeList {
        buffer: Vec<u8>,
    }

    impl AttributeList {
        fn new() -> Result<Self> {
            let mut size = 0usize;
            unsafe {
                InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut size);
            }
            if size == 0 {
                bail!(
                    "InitializeProcThreadAttributeList sizing failed with Win32 error {}",
                    unsafe { GetLastError() }
                );
            }
            let mut buffer = vec![0u8; size];
            let ok = unsafe {
                InitializeProcThreadAttributeList(buffer.as_mut_ptr().cast(), 1, 0, &mut size)
            };
            if ok == 0 {
                bail!(
                    "InitializeProcThreadAttributeList failed with Win32 error {}",
                    unsafe { GetLastError() }
                );
            }
            Ok(Self { buffer })
        }

        fn as_mut_ptr(&mut self) -> *mut c_void {
            self.buffer.as_mut_ptr().cast()
        }

        fn set_security_capabilities(
            &mut self,
            capabilities: &mut SECURITY_CAPABILITIES,
        ) -> Result<()> {
            let ok = unsafe {
                UpdateProcThreadAttribute(
                    self.as_mut_ptr(),
                    0,
                    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                    (capabilities as *mut SECURITY_CAPABILITIES).cast(),
                    size_of::<SECURITY_CAPABILITIES>(),
                    null_mut(),
                    null_mut(),
                )
            };
            if ok == 0 {
                bail!(
                    "UpdateProcThreadAttribute failed with Win32 error {}",
                    unsafe { GetLastError() }
                );
            }
            Ok(())
        }
    }

    impl Drop for AttributeList {
        fn drop(&mut self) {
            if !self.buffer.is_empty() {
                unsafe {
                    DeleteProcThreadAttributeList(self.as_mut_ptr());
                }
            }
        }
    }

    pub fn run() -> Result<i32> {
        let (request_json, prepare_only) = parse_request_arg()?;
        let request: Request =
            serde_json::from_str(&request_json).context("invalid sandbox request JSON")?;
        validate_request(&request)?;
        let profile_name = profile_name(&request);
        let sid = create_or_derive_profile(&profile_name)?;
        let sid_string = sid_to_string(sid.0)?;
        prepare_acl(&request, &sid_string)?;
        if prepare_only {
            println!(
                "AUTOMATA_SANDBOX_READY:{}",
                json!({
                    "backend": "windows-appcontainer",
                    "profile_hash": request.profile.profile_hash,
                })
            );
            return Ok(0);
        }
        spawn_appcontainer(&request, sid.0)
    }

    fn parse_request_arg() -> Result<(String, bool)> {
        let mut args = std::env::args().skip(1);
        let mut payload = None;
        let mut prepare_only = false;
        while let Some(argument) = args.next() {
            match argument.as_str() {
                "--request-json" => {
                    if payload.is_some() {
                        bail!("sandbox request was provided more than once");
                    }
                    payload = Some(
                        args.next()
                            .context("--request-json requires a JSON payload")?,
                    );
                }
                "--request-file" => {
                    if payload.is_some() {
                        bail!("sandbox request was provided more than once");
                    }
                    let path = PathBuf::from(
                        args.next()
                            .context("--request-file requires a filesystem path")?,
                    );
                    let contents = fs::read_to_string(&path)
                        .with_context(|| format!("could not read {}", path.display()))?;
                    fs::remove_file(&path)
                        .with_context(|| format!("could not delete {}", path.display()))?;
                    payload = Some(contents);
                }
                "--prepare-only" => prepare_only = true,
                _ => bail!("unknown sandbox host argument: {argument}"),
            }
        }
        Ok((
            payload.context("expected --request-json or --request-file")?,
            prepare_only,
        ))
    }

    fn validate_request(request: &Request) -> Result<()> {
        if request.schema_version != REQUEST_SCHEMA_VERSION {
            bail!("unsupported request schema version");
        }
        if request.argv.is_empty() || request.argv[0].trim().is_empty() {
            bail!("argv must not be empty");
        }
        if request.profile.sandbox_enforcement != "managed" {
            bail!("Windows sandbox host only accepts managed profiles");
        }
        if request.profile.network != "restricted" {
            bail!("online managed AppContainer profiles are not enabled");
        }
        if request.profile.workspace_roots.is_empty() {
            bail!("managed profile requires a workspace root");
        }
        let cwd = canonical(&request.cwd)?;
        let inside_workspace = request.profile.workspace_roots.iter().any(|root| {
            canonical(root)
                .map(|workspace| cwd.starts_with(workspace))
                .unwrap_or(false)
        });
        if !inside_workspace {
            bail!("cwd must stay inside a workspace root");
        }
        Ok(())
    }

    fn profile_name(request: &Request) -> String {
        let mut hasher = Sha256::new();
        hasher.update(b"automata-windows-appcontainer-v1");
        for root in &request.profile.workspace_roots {
            hasher.update(root.to_lowercase().as_bytes());
            hasher.update([0]);
        }
        let digest = format!("{:x}", hasher.finalize());
        format!("Automata.Sandbox.{}", &digest[..24])
    }

    fn create_or_derive_profile(name: &str) -> Result<OwnedSid> {
        let name_wide = wide(name);
        let display_wide = wide("Automata managed sandbox");
        let description_wide = wide("Isolated execution profile for Automata tools");
        let mut sid = null_mut();
        let create_result = unsafe {
            CreateAppContainerProfile(
                name_wide.as_ptr(),
                display_wide.as_ptr(),
                description_wide.as_ptr(),
                null(),
                0,
                &mut sid,
            )
        };
        if create_result >= 0 && !sid.is_null() {
            return Ok(OwnedSid(sid));
        }
        sid = null_mut();
        let derive_result =
            unsafe { DeriveAppContainerSidFromAppContainerName(name_wide.as_ptr(), &mut sid) };
        if derive_result < 0 || sid.is_null() {
            bail!(
                "failed to create or derive AppContainer profile (create={create_result:#x}, derive={derive_result:#x})"
            );
        }
        Ok(OwnedSid(sid))
    }

    fn sid_to_string(sid: *mut c_void) -> Result<String> {
        #[link(name = "advapi32")]
        extern "system" {
            fn ConvertSidToStringSidW(sid: *mut c_void, string_sid: *mut *mut u16) -> i32;
        }
        #[link(name = "kernel32")]
        extern "system" {
            fn LocalFree(memory: isize) -> isize;
        }
        let mut value = null_mut();
        let ok = unsafe { ConvertSidToStringSidW(sid, &mut value) };
        if ok == 0 || value.is_null() {
            bail!(
                "ConvertSidToStringSidW failed with Win32 error {}",
                unsafe { GetLastError() }
            );
        }
        let mut length = 0usize;
        unsafe {
            while *value.add(length) != 0 {
                length += 1;
            }
        }
        let result = String::from_utf16(unsafe { std::slice::from_raw_parts(value, length) })
            .context("AppContainer SID was not valid UTF-16")?;
        unsafe {
            LocalFree(value as isize);
        }
        Ok(result)
    }

    fn prepare_acl(request: &Request, sid: &str) -> Result<()> {
        for root in request
            .profile
            .workspace_roots
            .iter()
            .chain(request.profile.temporary_roots.iter())
        {
            let path = canonical(root)?;
            if !path.exists() {
                bail!("sandbox writable root does not exist: {}", path.display());
            }
            let ace = format!("*{sid}:(OI)(CI)M");
            run_icacls(&path, &["/grant", &ace, "/Q"])?;
        }
        for root in &request.profile.runtime_roots {
            let path = canonical(root)?;
            let ace = format!("*{sid}:(OI)(CI)RX");
            run_icacls(&path, &["/grant", &ace, "/Q"])?;
        }
        for protected in &request.profile.protected_paths {
            let path = PathBuf::from(protected);
            if path.exists() {
                protect_path(&path, sid, false)?;
            }
        }
        for denied in &request.profile.deny_read_paths {
            let path = PathBuf::from(denied);
            if path.exists() {
                protect_path(&path, sid, true)?;
            }
        }
        Ok(())
    }

    fn protect_path(path: &Path, sid: &str, deny_read: bool) -> Result<()> {
        let principal = format!("*{sid}");
        run_icacls(path, &["/inheritance:d", "/Q"])?;
        run_icacls(path, &["/remove:g", &principal, "/T", "/C", "/Q"])?;
        run_icacls(path, &["/remove:d", &principal, "/T", "/C", "/Q"])?;
        let rights = if deny_read {
            format!("*{sid}:(OI)(CI)F")
        } else {
            format!("*{sid}:(OI)(CI)(W,D,DC)")
        };
        run_icacls(path, &["/deny", &rights, "/Q"])
    }

    fn run_icacls(path: &Path, arguments: &[&str]) -> Result<()> {
        use std::os::windows::process::CommandExt;
        let output = Command::new("icacls.exe")
            .arg(path)
            .args(arguments)
            .creation_flags(0x0800_0000)
            .stdin(Stdio::null())
            .output()
            .with_context(|| format!("failed to run icacls for {}", path.display()))?;
        if !output.status.success() {
            bail!(
                "icacls could not apply sandbox ACL to {} (exit={:?}): {} {}",
                path.display(),
                output.status.code(),
                String::from_utf8_lossy(&output.stdout).trim(),
                String::from_utf8_lossy(&output.stderr).trim(),
            );
        }
        Ok(())
    }

    fn spawn_appcontainer(request: &Request, sid: *mut c_void) -> Result<i32> {
        let mut capabilities: SECURITY_CAPABILITIES = unsafe { zeroed() };
        capabilities.AppContainerSid = sid;
        capabilities.Capabilities = null_mut();
        capabilities.CapabilityCount = 0;
        capabilities.Reserved = 0;

        let mut attributes = AttributeList::new()?;
        attributes.set_security_capabilities(&mut capabilities)?;

        let stdin = std_handle(STD_INPUT_HANDLE)?;
        let stdout = std_handle(STD_OUTPUT_HANDLE)?;
        let stderr = std_handle(STD_ERROR_HANDLE)?;
        for handle in [stdin, stdout, stderr] {
            unsafe {
                SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT);
            }
        }

        let mut startup: STARTUPINFOEXW = unsafe { zeroed() };
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = stdin;
        startup.StartupInfo.hStdOutput = stdout;
        startup.StartupInfo.hStdError = stderr;
        startup.lpAttributeList = attributes.as_mut_ptr();

        let mut process_info: PROCESS_INFORMATION = unsafe { zeroed() };
        let mut command_line = wide(&windows_command_line(&request.argv));
        let cwd = wide(&request.cwd);
        let environment = environment_block(&request.env);
        let flags = EXTENDED_STARTUPINFO_PRESENT
            | CREATE_UNICODE_ENVIRONMENT
            | CREATE_SUSPENDED
            | CREATE_NEW_PROCESS_GROUP;
        let created = unsafe {
            CreateProcessW(
                null(),
                command_line.as_mut_ptr(),
                null(),
                null(),
                1,
                flags,
                environment.as_ptr().cast(),
                cwd.as_ptr(),
                &startup.StartupInfo,
                &mut process_info,
            )
        };
        if created == 0 {
            bail!("CreateProcessW failed with Win32 error {}", unsafe {
                GetLastError()
            });
        }
        let process = OwnedHandle::new(process_info.hProcess, "process handle")?;
        let thread = OwnedHandle::new(process_info.hThread, "thread handle")?;
        let job = create_kill_on_close_job()?;
        let assigned = unsafe { AssignProcessToJobObject(job.0, process.0) };
        if assigned == 0 {
            bail!(
                "AssignProcessToJobObject failed with Win32 error {}",
                unsafe { GetLastError() }
            );
        }
        if unsafe { ResumeThread(thread.0) } == u32::MAX {
            bail!("ResumeThread failed with Win32 error {}", unsafe {
                GetLastError()
            });
        }
        unsafe {
            WaitForSingleObject(process.0, INFINITE);
        }
        let mut exit_code = 1u32;
        let got_exit = unsafe { GetExitCodeProcess(process.0, &mut exit_code) };
        if got_exit == 0 {
            bail!("GetExitCodeProcess failed with Win32 error {}", unsafe {
                GetLastError()
            });
        }
        Ok(exit_code as i32)
    }

    fn create_kill_on_close_job() -> Result<OwnedHandle> {
        let job = OwnedHandle::new(
            unsafe { CreateJobObjectW(null(), null()) },
            "CreateJobObjectW",
        )?;
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if ok == 0 {
            bail!(
                "SetInformationJobObject failed with Win32 error {}",
                unsafe { GetLastError() }
            );
        }
        Ok(job)
    }

    fn std_handle(kind: u32) -> Result<HANDLE> {
        OwnedHandle::new(unsafe { GetStdHandle(kind) }, "GetStdHandle").map(|handle| {
            let raw = handle.0;
            std::mem::forget(handle);
            raw
        })
    }

    fn environment_block(environment: &BTreeMap<String, String>) -> Vec<u16> {
        let mut block = Vec::new();
        for (name, value) in environment {
            block.extend(OsStr::new(&format!("{name}={value}")).encode_wide());
            block.push(0);
        }
        block.push(0);
        block
    }

    fn windows_command_line(argv: &[String]) -> String {
        argv.iter()
            .map(|argument| quote_windows_argument(argument))
            .collect::<Vec<_>>()
            .join(" ")
    }

    fn quote_windows_argument(argument: &str) -> String {
        if !argument.is_empty()
            && !argument
                .chars()
                .any(|character| character.is_whitespace() || character == '"')
        {
            return argument.to_owned();
        }
        let mut result = String::from("\"");
        let mut backslashes = 0usize;
        for character in argument.chars() {
            if character == '\\' {
                backslashes += 1;
                continue;
            }
            if character == '"' {
                result.push_str(&"\\".repeat(backslashes * 2 + 1));
                result.push('"');
                backslashes = 0;
                continue;
            }
            result.push_str(&"\\".repeat(backslashes));
            backslashes = 0;
            result.push(character);
        }
        result.push_str(&"\\".repeat(backslashes * 2));
        result.push('"');
        result
    }

    fn canonical(value: impl AsRef<Path>) -> Result<PathBuf> {
        std::fs::canonicalize(value.as_ref())
            .with_context(|| format!("path is unavailable: {}", value.as_ref().display()))
    }

    fn wide(value: &str) -> Vec<u16> {
        OsStr::new(value).encode_wide().chain(Some(0)).collect()
    }

    pub fn report_and_exit(error: anyhow::Error) -> ! {
        let payload = json!({
            "code": classify_error(&error),
            "message": format!("{error:#}"),
        });
        eprintln!("AUTOMATA_SANDBOX_ERROR:{payload}");
        std::process::exit(125);
    }

    fn classify_error(error: &anyhow::Error) -> &'static str {
        let message = format!("{error:#}").to_lowercase();
        if message.contains("icacls")
            && (message.contains("access is denied") || message.contains("exit=some(5)"))
        {
            "sandbox_setup_required"
        } else if message.contains("icacls") || message.contains("appcontainer profile") {
            "sandbox_setup_failed"
        } else if message.contains("access") || message.contains("denied") {
            "sandbox_denied"
        } else if message.contains("schema") || message.contains("request") {
            "sandbox_protocol_error"
        } else {
            "sandbox_spawn_failed"
        }
    }
}

#[cfg(windows)]
fn main() {
    match windows_host::run() {
        Ok(code) => std::process::exit(code),
        Err(error) => windows_host::report_and_exit(error),
    }
}

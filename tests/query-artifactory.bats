#!/usr/bin/env bats

setup() {
    export PROJECT_ROOT
    PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/.." && pwd)"

    export SCRIPT="${PROJECT_ROOT}/query-artifactory"
    export TEST_ROOT="${BATS_TEST_TMPDIR}/query-artifactory"
    export HOME="${TEST_ROOT}/home"

    mkdir -p "${TEST_ROOT}/bin" "${HOME}/.ivy2"

    cat > "${HOME}/.ivy2/.credentials" <<'EOF'
realm=Artifactory Realm
host=rt.artifactory.tio.systems
user=white1
password=test-token
EOF

    cat > "${TEST_ROOT}/bin/curl" <<'EOF'
#!/usr/bin/env bash

printf '%s\n' "$*" >> "${TEST_ROOT}/curl.log"
if [[ "${MOCK_CURL_FAILURE:-}" == "1" ]]; then
    printf '%s\n' 'The requested URL returned error: 403' >&2
    exit 22
fi

printf '%s\n' '{"results":[{"repo":"sbt-recs-libs-local","path":"com/example","name":"example.pom"}]}'
EOF

    chmod +x "${TEST_ROOT}/bin/curl"
    export PATH="${TEST_ROOT}/bin:${PATH}"
}

@test 'lists repository contents using the rt Artifactory host and credentials token' {
    run bash "${SCRIPT}"

    [ "$status" -eq 0 ]
    [[ "$output" == *" - example.pom"* ]]
    grep -q 'rt.artifactory.tio.systems' "${TEST_ROOT}/curl.log"
    grep -q 'X-JFrog-Art-Api: test-token' "${TEST_ROOT}/curl.log"
}

@test 'reports an Artifactory authentication failure' {
    export MOCK_CURL_FAILURE=1

    run bash "${SCRIPT}"

    [ "$status" -ne 0 ]
    [[ "$output" == *"Artifactory request failed"* ]]
}

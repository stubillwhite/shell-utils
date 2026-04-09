#!/usr/bin/env bats

setup() {
    export PROJECT_ROOT
    PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/.." && pwd)"

    export SCRIPT="${PROJECT_ROOT}/git-list-file-changes"
    export TEST_ROOT="${BATS_TEST_TMPDIR}/workspace"

    mkdir -p "${TEST_ROOT}"
}

init-repo() {
    local repo=$1

    mkdir -p "${repo}"

    pushd "${repo}" > /dev/null || return 1
    git init -q
    git config user.name "Test User"
    git config user.email "test@example.com"
    popd > /dev/null || return 1
}

commit-all() {
    local repo=$1
    local message=$2
    local when=$3

    pushd "${repo}" > /dev/null || return 1
    git add -A
    GIT_AUTHOR_DATE="${when}" \
    GIT_COMMITTER_DATE="${when}" \
        git commit -qm "${message}"
    popd > /dev/null || return 1
}

run-in-repo() {
    local repo=$1

    pushd "${repo}" > /dev/null || return 1
    run bash "${SCRIPT}"
    popd > /dev/null || return 1
}

@test 'fails outside a git repository' {
    local dir="${TEST_ROOT}/not-a-repo"
    mkdir -p "${dir}"

    pushd "${dir}" > /dev/null || false
    run bash "${SCRIPT}"
    popd > /dev/null || false

    [ "$status" -eq 1 ]
    [ "$output" = 'git-list-file-changes: not inside a git repository' ]
}

@test 'lists untracked modified and deleted files in descending timestamp order' {
    local repo="${TEST_ROOT}/mixed-repo"
    init-repo "${repo}"

    printf 'one\ntwo\nthree\n' > "${repo}/modified.txt"
    printf 'remove me\n' > "${repo}/deleted.txt"
    commit-all "${repo}" 'Initial commit' '2025-01-02T03:04:05+0000'

    printf 'one\ntwo changed\nthree\nfour\n' > "${repo}/modified.txt"
    touch -t 202504050607.08 "${repo}/modified.txt"

    rm "${repo}/deleted.txt"

    printf 'brand new\n' > "${repo}/untracked.txt"
    touch -t 202504050607.09 "${repo}/untracked.txt"

    run-in-repo "${repo}"

    [ "$status" -eq 0 ]
    [ "${#lines[@]}" -eq 3 ]
    [ "${lines[0]}" = '05-04-2025 06:07:09 ?? 100% ██████████ untracked.txt' ]
    [ "${lines[1]}" = '05-04-2025 06:07:08 M   50% █████░░░░░ modified.txt' ]
    [[ "${lines[2]}" =~ ^[0-9]{2}-[0-9]{2}-[0-9]{4}\ [0-9]{2}:[0-9]{2}:[0-9]{2}\ D[[:space:]]+100%\ ██████████\ deleted.txt$ ]]
}

@test 'lists staged renames with the destination path' {
    local repo="${TEST_ROOT}/renamed-repo"
    init-repo "${repo}"

    printf 'rename me\n' > "${repo}/old.txt"
    commit-all "${repo}" 'Initial commit' '2025-01-02T03:04:05+0000'

    git -C "${repo}" mv old.txt new.txt
    touch -t 202504050607.10 "${repo}/new.txt"

    run-in-repo "${repo}"

    [ "$status" -eq 0 ]
    [ "${#lines[@]}" -eq 1 ]
    [ "${lines[0]}" = '05-04-2025 06:07:10 R  100% ██████████ new.txt' ]
}

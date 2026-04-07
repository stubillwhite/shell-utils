#!/usr/bin/env bats

setup() {
    export PROJECT_ROOT
    PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/.." && pwd)"

    export SCRIPT="${PROJECT_ROOT}/git-open"
    export TEST_ROOT="${BATS_TEST_TMPDIR}/workspace"

    mkdir -p "${TEST_ROOT}/bin"

    cat > "${TEST_ROOT}/bin/ssh" <<'EOF'
#!/usr/bin/env bash

if [[ "$1" == "-G" ]]; then
    case "$2" in
        github-personal)
            printf 'hostname github.com\n'
            ;;
        *)
            printf 'hostname %s\n' "$2"
            ;;
    esac
fi
EOF

    cat > "${TEST_ROOT}/bin/open" <<'EOF'
#!/usr/bin/env bash

printf '%s\n' "$1" >> "${TEST_ROOT}/open.log"
printf '%s\n' "$1"
EOF

    chmod +x "${TEST_ROOT}/bin/ssh"
    chmod +x "${TEST_ROOT}/bin/open"
    export PATH="${TEST_ROOT}/bin:${PATH}"
}

create-repo-with-url() {
    local url=$1
    local repo="${TEST_ROOT}/my-repo"

    mkdir -p "${repo}/subdir"

    pushd "${repo}" > /dev/null || return 1
    git init > /dev/null
    git config user.name "Test User"
    git config user.email "test@example.com"
    git remote add origin "${url}"
    touch file-one.md subdir/file-two.md
    git add .
    git commit -m 'Initial commit' > /dev/null
    git branch -M main > /dev/null
    popd > /dev/null || return 1
}

checkout-branch() {
    local branch=$1

    git -C "${TEST_ROOT}/my-repo" checkout -b "${branch}" > /dev/null
}

run-test() {
    local url=$1
    local branch=$2
    local target=$3
    local expected=$4

    create-repo-with-url "${url}"

    if [[ "${branch}" != "main" ]]; then
        checkout-branch "${branch}"
    fi

    run bash "${SCRIPT}" "${TEST_ROOT}/my-repo/${target}"
    [ "$status" -eq 0 ]
    [ "$output" = "${expected}" ]
}

@test 'github ssh file within repository root on main' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'main' \
             'file-one.md' \
             'https://github.com/my-username/my-repo/blob/main/file-one.md'
}

@test 'github ssh file within sub-directory on branch' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'my-branch' \
             'subdir/file-two.md' \
             'https://github.com/my-username/my-repo/blob/my-branch/subdir/file-two.md'
}

@test 'github ssh directory uses tree URLs' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'my-branch' \
             'subdir' \
             'https://github.com/my-username/my-repo/tree/my-branch/subdir'
}

@test 'github ssh repository root uses tree URL' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'main' \
             '.' \
             'https://github.com/my-username/my-repo/tree/main'
}

@test 'bitbucket file within sub-directory on branch' {
    run-test 'https://bitbucket.org/my-team/my-repo.git' \
             'my-branch' \
             'subdir/file-two.md' \
             'https://bitbucket.org/my-team/my-repo/src/my-branch/subdir/file-two.md'
}

@test 'github https repository root uses current branch when no path is provided' {
    create-repo-with-url 'https://github.com/my-org/my-repo.git'
    checkout-branch 'my-branch'

    pushd "${TEST_ROOT}/my-repo" > /dev/null || false
    run bash "${SCRIPT}"
    popd > /dev/null || false

    [ "$status" -eq 0 ]
    [ "$output" = 'https://github.com/my-org/my-repo/tree/my-branch' ]
}

@test 'opens resolved url with open by default' {
    create-repo-with-url 'https://github.com/my-org/my-repo.git'

    run bash "${SCRIPT}" "${TEST_ROOT}/my-repo/file-one.md"

    [ "$status" -eq 0 ]
    [ "$output" = 'https://github.com/my-org/my-repo/blob/main/file-one.md' ]
    [ "$(cat "${TEST_ROOT}/open.log")" = 'https://github.com/my-org/my-repo/blob/main/file-one.md' ]
}

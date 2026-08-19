#!/usr/bin/env bats

setup() {
    export PROJECT_ROOT
    PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/.." && pwd)"

    export SCRIPT="${PROJECT_ROOT}/github-list-pull-requests"
    export TEST_ROOT="${BATS_TEST_TMPDIR}/workspace"
    export SECRET_GITHUB_TOKEN="test-token"

    mkdir -p "${TEST_ROOT}/bin"

    cat > "${TEST_ROOT}/bin/curl" <<'EOF'
#!/usr/bin/env bash

url="${@: -1}"

case "${url}" in
    *"author%3Astuw-els+type%3Apr&per_page=100&page=0")
        printf 'SEARCH_MINE\n'
        ;;
    *"repo%3Aocto/demo+type%3Apr&per_page=100&page=0"|*"repo%3Aocto%2Fdemo+type%3Apr&per_page=100&page=0")
        printf 'SEARCH_REPO\n'
        ;;
    */pulls/1/reviews)
        printf 'REVIEWS_ONE\n'
        ;;
    */pulls/2/reviews)
        printf 'REVIEWS_ZERO\n'
        ;;
    */pulls/1)
        printf 'PR_ONE\n'
        ;;
    */pulls/2)
        printf 'PR_TWO\n'
        ;;
    *)
        printf 'unexpected curl url: %s\n' "${url}" >&2
        exit 1
        ;;
esac
EOF

    cat > "${TEST_ROOT}/bin/jq" <<'EOF'
#!/usr/bin/env bash

input="$(cat)"

if [[ "$*" == *".incomplete_results"* ]]; then
    printf 'false\n'
elif [[ "$*" == *".items[].pull_request.url"* ]]; then
    case "${input}" in
        SEARCH_MINE)
            printf 'https://api.github.com/repos/octo/demo/pulls/1\n'
            ;;
        SEARCH_REPO)
            printf 'https://api.github.com/repos/octo/demo/pulls/1\n'
            printf 'https://api.github.com/repos/octo/demo/pulls/2\n'
            ;;
        *)
            printf 'unexpected search input: %s\n' "${input}" >&2
            exit 1
            ;;
    esac
elif [[ "$*" == *"length"* ]]; then
    case "${input}" in
        REVIEWS_ONE)
            printf '1\n'
            ;;
        REVIEWS_ZERO)
            printf '0\n'
            ;;
        *)
            printf 'unexpected reviews input: %s\n' "${input}" >&2
            exit 1
            ;;
    esac
elif [[ "$*" == *"| @tsv"* ]]; then
    reviews=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --arg)
                if [[ "$2" == "reviews" ]]; then
                    reviews="$3"
                    shift 3
                    continue
                fi
                ;;
        esac
        shift
    done

    if [[ "${reviews}" -gt 0 ]]; then
        review_status='has-reviews'
    else
        review_status='no-reviews'
    fi

    case "${input}" in
        PR_ONE)
            printf 'demo\t%s\thas-comments\thttps://github.com/octo/demo/pull/1\t"Mine PR"\n' "${review_status}"
            ;;
        PR_TWO)
            printf 'demo\t%s\tno-comments\thttps://github.com/octo/demo/pull/2\t"Repo PR"\n' "${review_status}"
            ;;
        *)
            printf 'unexpected pr input: %s\n' "${input}" >&2
            exit 1
            ;;
    esac
else
    printf 'unexpected jq invocation: %s\n' "$*" >&2
    exit 1
fi
EOF

    cat > "${TEST_ROOT}/bin/column" <<'EOF'
#!/usr/bin/env bash
cat
EOF

    cat > "${TEST_ROOT}/bin/gsed" <<'EOF'
#!/usr/bin/env bash
cat
EOF

    cat > "${TEST_ROOT}/bin/ssh" <<'EOF'
#!/usr/bin/env bash

if [[ "$1" == "-G" ]]; then
    printf 'hostname github.com\n'
fi
EOF

    chmod +x "${TEST_ROOT}/bin/curl" "${TEST_ROOT}/bin/jq" "${TEST_ROOT}/bin/column" "${TEST_ROOT}/bin/gsed" "${TEST_ROOT}/bin/ssh"
    export PATH="${TEST_ROOT}/bin:${PATH}"
}

create-repo() {
    local remote_url=$1
    local repo="${TEST_ROOT}/repo"

    mkdir -p "${repo}"

    pushd "${repo}" > /dev/null || return 1
    git init -q
    git config user.name "Test User"
    git config user.email "s.white.1@elsevier.com"
    git remote add origin "${remote_url}"
    popd > /dev/null || return 1
}

run-in-repo() {
    local subcommand=$1

    pushd "${TEST_ROOT}/repo" > /dev/null || return 1
    run zsh "${SCRIPT}" "${subcommand}"
    popd > /dev/null || return 1
}

@test 'requires a supported subcommand' {
    create-repo 'git@github.com:octo/demo.git'

    pushd "${TEST_ROOT}/repo" > /dev/null || false
    run zsh "${SCRIPT}"
    popd > /dev/null || false

    [ "$status" -eq 1 ]
    [ "${lines[0]}" = 'Usage: github-list-pull-requests <mine|repo>' ]
}

@test 'lists pull requests for the configured current user via mine subcommand' {
    create-repo 'git@github.com:octo/demo.git'

    run-in-repo mine

    [ "$status" -eq 0 ]
    [ "${lines[0]}" = 'Pull requests for stuw-els:' ]
    [ "${lines[1]}" = $'demo\thas-reviews\thas-comments\thttps://github.com/octo/demo/pull/1\t"Mine PR"' ]
}

@test 'lists repository pull requests for all users via repo subcommand' {
    create-repo 'git@github.com:octo/demo.git'

    run-in-repo repo

    [ "$status" -eq 0 ]
    [ "${lines[0]}" = 'Pull requests for octo/demo:' ]
    [ "${lines[1]}" = $'demo\thas-reviews\thas-comments\thttps://github.com/octo/demo/pull/1\t"Mine PR"' ]
    [ "${lines[2]}" = $'demo\tno-reviews\tno-comments\thttps://github.com/octo/demo/pull/2\t"Repo PR"' ]
}

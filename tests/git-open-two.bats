#!/usr/bin/env bats

function __construct-url() {
    local pathInRepo=$1
    local branch=$2
    local url=$3

    if [[ $url =~ ^git@ ]]; then
        local hostAlias=$(echo "$url" | sed -E "s|git@(.*):(.*).git|\1|")
        local hostname=$(ssh -G "${hostAlias}" | awk '$1 == "hostname" { print $2 }')

        echo "$url" \
            | sed -E "s|git@(.*):(.*).git|https://${hostname}/\2/blob/${branch}/${pathInRepo}|"

    elif [[ $url =~ ^https://bitbucket.org ]]; then

        echo "$url" \
            | sed -E "s|(.*).git|\1/src/${branch}/${pathInRepo}|"

    elif [[ $url =~ ^https://github.com ]]; then
        [[ -n "${pathInRepo}" ]] && pathInRepo="blob/${branch}/${pathInRepo}"

        echo "$url" \
            | sed -E "s|(.*).git|\1/${pathInRepo}|"

    else
        echo "Failed to open due to unrecognised url '$url'"
    fi
}

function __git-url() {
    local filename=$1

    local pathInRepo 

    # If opening a particular artifact then move to it to ensure we're querying the right Git repo
    if [[ -n "${filename}" ]]; then
        local containingDirectory

        if [ -d "${filename}" ]; then 
            containingDirectory="${filename}"
        else 
            containingDirectory=$(dirname "${filename}")
        fi

        pushd "${containingDirectory}" > /dev/null
        pathInRepo=$(git ls-tree --full-name --name-only HEAD $(basename "${filename}"))
    fi

    __construct-url "${pathInRepo}" "${branch}" "${url}"

    [[ -n "${filename}" ]] && popd > /dev/null
}

function create-repo-with-url() {
    local url=$1

    rm -rf my-repo
    mkdir -p my-repo/subdir
    pushd my-repo
    git init
    git remote add origin "${url}"
    touch file-one.md
    touch subdir/file-two.md
    git add .
    git commit -am 'Initial commit'
    git checkout -b my-branch 2>&1
    popd
}

function checkout-branch() {
    local branch=$1
    pushd my-repo
    git checkout "${branch}" 2>&1
    popd
}

function teardown() {
    rm -rf my-repo
}

function run-test() {
    local url=$1
    local branch=$2
    local file=$3
    local expected=$4

    create-repo-with-url "${url}" > /dev/null
    checkout-branch "${branch}" > /dev/null
    run __git-url "${file}"
    echo "Expected: ${expected}"
    echo "Actual:   ${lines}"
    [ "${lines[0]}" = "${expected}" ]
}

@test 'git ssh file within directory on main' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'main'  \
             'my-repo/file-one.md' \
             'https://github.com/my-username/my-repo/blob/main/file-one.md'
}

@test 'git ssh file within sub-directory on main' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'main'  \
             'my-repo/subdir/file-two.md' \
             'https://github.com/my-username/my-repo/blob/main/subdir/file-two.md'
}

@test 'git ssh root directory on main' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'main'  \
             'my-repo' \
             'https://github.com/my-username/my-repo/'
}

@test 'git ssh file within directory on branch' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'my-branch'  \
             'my-repo/file-one.md' \
             'https://github.com/my-username/my-repo/blob/my-branch/file-one.md'
}

@test 'git ssh file within sub-directory on branch' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'my-branch'  \
             'my-repo/subdir/file-two.md' \
             'https://github.com/my-username/my-repo/blob/my-branch/subdir/file-two.md'
}

@test 'git ssh root directory on branch' {
    run-test 'git@github-personal:my-username/my-repo.git' \
             'my-branch'  \
             'my-repo' \
             'https://github.com/my-username/my-repo/blob/my-branch'
}

@test 'bitbucket file within sub-directory on branch' {
    run-test 'https://bitbucket.org/my-repo.git' \
             'my-branch'  \
             'my-repo/subdir/file-two.md' \
             'https://bitbucket.org/my-repo/src/my-branch/subdir/file-two.md'
}

@test 'github file within sub-directory on branch' {
    run-test 'https://github.com/my-repo.git' \
             'my-branch'  \
             'my-repo/subdir/file-two.md' \
             'https://github.com/my-repo/blob/my-branch/subdir/file-two.md'
}


import React, { useState } from "react";

const REPO_PATTERN =
  /^(?:https?:\/\/|git@)?(?:www\.)?github\.com[:/][A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\/[A-Za-z0-9._-]+?(?:\.git)?\/?$/;
const BRANCH_INVALID = /[~^:?*[\]\\\s]/;

const DEFAULTS = {
  repoUrl: "",
  authorName: "",
  branchName: "",
  token: "",
  push: true,
  runTests: true,
  useAi: true,
};

function validate(values) {
  const errors = {};
  if (!values.repoUrl.trim()) errors.repoUrl = "Repository URL is required.";
  else if (!REPO_PATTERN.test(values.repoUrl.trim()))
    errors.repoUrl = "Must be a GitHub URL, e.g. https://github.com/owner/repo";

  if (!values.authorName.trim()) errors.authorName = "Your name is required.";

  const branch = values.branchName.trim();
  if (!branch) errors.branchName = "Branch name is required.";
  else if (BRANCH_INVALID.test(branch))
    errors.branchName = "No spaces or ~ ^ : ? * [ ] \\ characters.";
  else if (branch.startsWith("-") || branch.includes(".."))
    errors.branchName = "Not a valid git branch name.";

  if (values.push && !values.token.trim())
    errors.token = "A token is required to push. Untick 'Push branch' to dry-run.";

  return errors;
}

export default function AnalyzeForm({ onStart, running }) {
  const [values, setValues] = useState(DEFAULTS);
  const [errors, setErrors] = useState({});
  const [touched, setTouched] = useState(false);

  const set = (key) => (event) => {
    const value =
      event.target.type === "checkbox" ? event.target.checked : event.target.value;
    const next = { ...values, [key]: value };
    setValues(next);
    if (touched) setErrors(validate(next));
  };

  const submit = (event) => {
    event.preventDefault();
    setTouched(true);
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    onStart({
      repo_url: values.repoUrl.trim(),
      author_name: values.authorName.trim(),
      branch_name: values.branchName.trim(),
      github_token: values.token.trim() || null,
      push: values.push,
      run_tests: values.runTests,
      use_ai: values.useAi,
    });
  };

  const field = (key, label, extra = {}) => (
    <div className={`field ${errors[key] ? "invalid" : ""}`}>
      <label htmlFor={key}>{label}</label>
      <input
        id={key}
        type={extra.password ? "password" : "text"}
        value={values[key]}
        onChange={set(key)}
        placeholder={extra.placeholder}
        autoComplete={extra.password ? "off" : "on"}
        spellCheck="false"
        disabled={running}
      />
      {extra.help && !errors[key] && <div className="help">{extra.help}</div>}
      {errors[key] && <div className="error">{errors[key]}</div>}
    </div>
  );

  return (
    <form className="card" onSubmit={submit}>
      <header>
        <h2>Run the agent</h2>
      </header>

      {field("repoUrl", "GitHub repository URL", {
        placeholder: "https://github.com/owner/repo",
      })}
      {field("authorName", "Your name", {
        placeholder: "Ada Lovelace",
        help: "Recorded as the commit author.",
      })}
      {field("branchName", "New branch name", {
        placeholder: "heal/auto-fixes",
        help: "The agent creates this branch and pushes fixes to it.",
      })}
      {field("token", "GitHub token", {
        password: true,
        placeholder: "ghp_… or github_pat_…",
        help: "Needs 'repo' scope, or fine-grained Contents: read & write.",
      })}
      <div className="token-note">
        <span aria-hidden="true">🔒</span>
        <span>
          The token is used only for this run. It is never written to logs, the
          job record, or the report — every response is scrubbed of
          credential-shaped strings before it leaves the server.
        </span>
      </div>

      <div className="checks">
        <label>
          <input type="checkbox" checked={values.push} onChange={set("push")} disabled={running} />
          Push branch
        </label>
        <label>
          <input type="checkbox" checked={values.runTests} onChange={set("runTests")} disabled={running} />
          Run tests
        </label>
        <label>
          <input type="checkbox" checked={values.useAi} onChange={set("useAi")} disabled={running} />
          AI repairs
        </label>
      </div>

      <button className="submit" type="submit" disabled={running}>
        {running ? "Agent running…" : "Analyze & heal repository"}
      </button>
    </form>
  );
}

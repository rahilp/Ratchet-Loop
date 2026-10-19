# Ratchet Loop

AI coding agents are fast, but they'll tell you something works when it doesn't. Ratchet Loop makes them prove it.

You describe a feature. Your agent splits it into small **checkpoints** and builds them one at a time. Each checkpoint has to get through four **gates** ([details](#the-four-gates)) before the next one starts: its tests pass, it looks like the design, a second AI (from another company if you have one installed) tries to break it and can't, and you sign off at the end. If a gate fails, the agent fixes the problem and tries again. It can't argue its way past.

You don't type any commands. You talk to your agent like normal, and it runs Ratchet for you.

## Step 1: Install Ratchet (once)

Ratchet works in Claude Code, Codex, Gemini CLI and GitHub Copilot CLI. You install it once per computer, not once per project. After that it's available in every project you open.

**1. Ask your agent to install it.** Open your agent (in any folder) and paste this:

> Please install Ratchet Loop from https://github.com/rahilp/Ratchet-Loop and set it up for this CLI.

Already cloned it? Give the folder path instead of the link. The agent will ask before it changes anything. Part of the setup is adding Ratchet's hooks to your CLI. Hooks are what stop the agent from skipping a gate, so they matter.

**2. Restart your CLI.** Hooks only load when the CLI starts, so the agent will ask you to close it and open it again. Do that, then say:

> Set up Ratchet and continue

The agent checks the hooks are working, tells you Ratchet is ready, and asks what you'd like to build. Tell it right there, or come back any time.

The hooks do nothing in projects where you're not using Ratchet, so they won't get in the way of your other work. If you use more than one of these CLIs, ask each one to install Ratchet for itself.

Prefer to keep Ratchet's setup inside a single project instead? Ask your agent to "install Ratchet Loop for this project only".

You'll need your project in a git repository, and Python 3, which most computers already have. If you're building screens or pages, Ratchet also uses Node and Playwright to take screenshots. The agent will offer to set those up if they're missing.

## Step 2: Build something

Any time you want something built, open your agent in your project folder and say:

> Use Ratchet to build this feature: <describe it in a sentence or two>.

A real one looks like this:

> Use Ratchet to build this feature: managers can approve or reject their team's leave requests, with a reason.

That's all you have to type. The agent takes it from there. It may ask a few quick questions before it shows you the plan, and it asks again whenever it needs you.

## What happens next

First the agent does some quick checks on its own. Are the hooks working? Which other AI tools are installed that could review the work? Has your team already written a spec for this? If anything needs your OK, like installing the hooks or starting a new git branch, it asks. If it finds a problem that would get in the way (the app won't start, there's no way to run tests, the screenshot tool is missing, or you have unsaved changes), it tells you in one message and offers to fix each one.

Then it shows you the plan, right in the chat. It opens with the objective: who the result is for, what they want, what done means to them, and what's out of scope (for example users: "managers who approve leave requests"; goal: "clear a team's leave requests without leaving the app"; done means: "a manager can approve or reject a request with a reason"; out of scope: "approving another team's requests"). Every step, every reviewer and every finding points back to it, so the build stays aimed at what you actually asked for. Below that you'll see each step, what "done" means for it, whether its look will be checked, how risky it is and who will review it. Read it. Say "yes" to start, or tell it what to change. If the feature has new screens, you also get a clickable prototype to look at first, so you can see what it'll look like before anything is built. Once you say yes, the plan, the design file and the prototype are locked. The agent can't change them later without asking you.

Now you can walk away. For each step the agent writes the tests first and makes sure they fail. Then it writes the code until they pass. Then the reviewers go looking for problems, and anything they find gets fixed and checked again. Every finished step becomes its own git commit, and you get a one-line update as each one lands.

If a step gets stuck, the agent comes back to you. Each gate gets a limited number of tries, and when they run out the run stops. The agent quotes the problem and gives you two or three options, labelled A, B and C, with the one it recommends. Just reply with the letter. Nothing gets lowered or skipped unless you pick it.

When everything is built, it tells you what to try. It also lists any small issues the reviewers noted but didn't hold things up for, and asks if you want any of them fixed. Try it. Say "approved" if you're happy. If you're not, say what's wrong in your own words. Your changes become new steps, and they go through the same gates.

So you're needed twice: once to OK the plan and once at the end. If you'd rather check every step, or not be asked at all after the plan, just say so.

## What Ratchet sets up for you

You don't have to prepare anything before your first run. If your project is missing something Ratchet needs, the agent makes it. Here's what it creates, and you're free to read and edit all of it.

**A design file (`DESIGN.md`).** This is your app's look written down exactly: colours, fonts, sizes, spacing, corner radius, and how each component looks in each state (normal, hover, disabled, error and so on). The prototype, the code and the screen reviewers all work from it, so a button comes out the same every time. If you don't have one, the agent writes it before the plan:

- for an existing app, it reads your real styles and components and writes down what you actually use, including the places where your app isn't consistent with itself (so reviewers don't flag you for matching your own old screens);
- for a new app, it asks a couple of questions (who it's for, the feel, any brand colour) and picks exact values, with text contrast that meets accessibility guidelines.

If a new screen needs something the design file doesn't cover yet, like a new kind of button, it's added before you see the plan, so you approve it along with everything else.

**Coding standards (`.ratchet/standards.md`).** The rules reviewers check the code against: where business logic lives, how errors are handled, naming, what may import what, security checks. If your project doesn't have written standards, the agent drafts 15 to 40 of them from how your code already works, with a real file as the example for each. It's marked as a draft. Read it and change anything you disagree with.

**A clickable prototype,** for features with new screens and nothing to copy. You see it before you approve the plan.

**Lessons (`.ratchet/learnings.md`).** Whenever you give feedback that applies beyond this one feature ("always use the shared date picker", say), it's written here, and every agent reads it before starting work. You shouldn't have to say the same thing twice.

**Automatic checks,** when you say yes to turning a repeated mistake into one. They're added to your project and run with the tests from then on. Building one may add a lint or test tool to your project's dev dependencies.

**A record of what's been built,** if you said yes to that (see the spec section below).

You can also ask for the design file on its own, without building anything:

> Create a DESIGN.md for this app from the existing screens.

## More things to ask for

Some other requests that work well:

> Use Ratchet to build this feature: customers can save items to a wishlist and get an email when one goes on sale.

> Use Ratchet to rebuild the old settings screen in the new design. The old screen is at src/legacy/Settings.tsx.

> Use Ratchet to refactor the billing module so it no longer talks to the database directly. Nothing the user sees should change.

Keep each request to one feature. Something you can explain in a few sentences usually turns into three to eight steps. If it's bigger than that, split it.

## Things you can say while it works

You can say any of these at any time, in your own words.

> Where are we?

> Pause the build, and resume it when I say so.

> Cancel this run.

The agent double-checks before it cancels anything.

> Only check in with me at the end, not after every checkpoint.

> Give me the viewer link, and open it if you can.

The viewer is a web page that shows the plan, every test and every review as it happens. You don't need it, since everything also shows up in the chat, but some people like to watch.

> Which reviewers and providers are available on this machine?

> Check that Ratchet's hooks are installed for this CLI, and install them if they are not.

> Set up a director for these three lanes, and ask me what you need to know.

That last one is for running several features at once, each in its own copy of the project, with one session keeping an eye on all of them.

### Saying yes, and saying no

The agent only moves forward on a clear yes to a question Ratchet asked. When it needs your approval (for the plan, a finished step, or anything else it has to check with you), it shows you the question and the replies that count as yes:

> yes / yes, go ahead / go ahead / approved

Capitals and a full stop or exclamation mark at the end don't matter, but nothing else can be attached. A thumbs-up, an "ok", a "sure, I guess", or a yes with a "but" on the end doesn't count. Only your first reply after the question counts. If it isn't one of those, nothing is approved: the agent answers what you said or makes the change you asked for, and asks again when it's ready. A yes you typed before the question was shown doesn't count either. And a yes only answers the question it belongs to. Saying yes to "only check in at the end?" doesn't also approve the plan. The agent asks Ratchet's question on its own, at the end of its message, with no other question beside it; Ratchet can't see the agent's message, so this part is up to the agent: Ratchet doesn't check that the question was shown alone (a known limit). Only a reply in the chat the run belongs to counts (or, when you move a run to a new chat, in the chat whose agent asked to take it over): typing "yes" in another chat open on the same project doesn't approve anything here. The run belongs to the chat that started it. On a project's very first run, when your CLI's hooks are running, that is the chat the agent ran Ratchet from (only an actual Ratchet command counts, run within the last minute; a chat that merely mentions one, say in an echo or a commit message, doesn't); otherwise it is the first chat you type in after the run starts. Ratchet does one thing at a time: while one of its steps runs (your tests, say, which can take minutes), any other step that would change the run is turned away with "Ratchet is busy running ...; try again when it finishes" and changes nothing. Your reply is still recorded straight away, even then. So if you say yes to something while the tests run, the agent tells you Ratchet is busy and makes the change once the tests finish, using the yes you already gave; you don't need to send it again. A reply can go unrecorded, though, in two cases: Ratchet can't trust its own records (its file of hashes is damaged, or its log was changed outside Ratchet), or its prompt hook fails. Then Ratchet doesn't record your reply at all, so it approves nothing and answers no question. The agent tells you so. Once the records are repaired (usually by running accept-state yourself in a terminal, which asks you to type back a short code), the agent asks its question again and you send your reply again. Messages your CLI sends on its own are never taken as your reply either: a turn where any of its lines starts (after any leading spaces and `<system-reminder>` tags on that line) with one of the markers `<agent-message from="...">` (the real wrapper a sub-agent's hand-back actually arrives in; any attribution value counts, matched by its opening tag), `Another Claude session sent a message:` (Claude Code's on-screen rendering of that same hand-back, kept too for a run or a host that still only shows this text), `<task-notification>` or `[SYSTEM NOTIFICATION - NOT USER INPUT]` (a background task's notice) is not recorded, so it answers no question and approves nothing. There is no limit on how many lines of ordinary text come before the marker line: piling on preamble does not get it recorded. Only the start of a line is checked: a message of yours that mentions one of these phrases mid-sentence is recorded as usual. If a real reply of yours has one of these markers at the start of one of its lines, Ratchet ignores it on purpose (when it can't tell, it doesn't record), so send it again without the marker at the start.

You can also tell it to build without hooks, but then the gates are only suggestions. It will explain that and ask first, and it only goes ahead if you reply with exactly this:

> yes, go ahead without hooks

That reply only counts if Ratchet's prompt hook is running, because that hook is how Ratchet hears you. If none of its hooks run in your CLI at all, the agent will tell you to install them.

### Accepting a review finding yourself

Sometimes a review round blocks on something you judge isn't worth another round: a narrow edge case, a cost you've decided to live with, a finding you disagree with. You can say so, once, with your own reason, and move on. The agent can't do this on its own; it can only ask, and only the open (blocking) findings of the round that's actually stuck can be asked about. It shows you each finding and the exact reply that accepts it:

> accept: <your reason>

Only your first reply after that question, in those exact words plus your own reason, accepts the findings it named; anything else accepts nothing. If two reviewers of the same round happened to report a finding with the same id, naming just that bare id is refused as ambiguous; say which reviewer's you mean (`reviewer/id`, e.g. `codex-correctness/A1`) and it accepts exactly that one. Each one you accept is written down with your reason, in `.ratchet/learnings.md`, in the step's list of findings left for later, and in that round's own record, so you (and later reviews) can see why it was let through. Accept every open finding of a blocked round and the step passes without another round of review; accept only some and the step stays blocked on the rest. If the code changes afterwards, normal review rules apply again, findings and all: accepting something once never exempts the code that comes after it.

In a director-enrolled lane, a chat reply never accepts a finding: the director must sign it instead (`director-sign accept-finding`), the same as every other approval in that lane.

### Raising a step's review limit

When a step hits its review-round limit, the agent offers you a short menu: simplify the step, change it so the review notes no longer apply, or stop here and keep what's already finished. Raising the limit so the reviewers get one more round is always listed last, and only once per step, ever — ask for it a second time on the same step and the agent won't offer it again, however many rounds `.ratchet/config.json` allows elsewhere. If you want to raise it, say so with your own reason:

> raise: <your reason>

Only your first reply after the question, in those exact words plus your reason, raises it; anything else raises nothing. Your reason is written down in `.ratchet/learnings.md`, the same as an accepted finding, so later rounds and agents see it too. Editing `max_adversarial_rounds` in `.ratchet/config.json` afterwards never lifts a step's own raised cap: once it's used, it's used, for that step, for the rest of this run.

Raising isn't the only way a step gets another look. Here is what each choice at the limit gives you:

| Your choice | More review rounds for this step |
|---|---|
| Simplify the step | One, once per step |
| Change the step | One, once per step |
| Raise the limit (`raise: <your reason>`) | One, once per step |
| Accept the open findings (`accept: <your reason>`) | None: the step passes without another round |
| Stop here | None |

## If your team writes specs (OpenSpec, Spec Kit, BMAD)

If your project already uses OpenSpec, GitHub Spec Kit or the BMAD Method, Ratchet builds from the spec you've written instead of making up its own plan:

> Use Ratchet to build the add-dark-mode change.

> Use Ratchet to build story 1.3.

The spec says what to build. Ratchet makes sure it was actually built. Every scenario in the spec gets a test that has to pass, every task gets assigned to a step, and if anything is left out, the plan tells you why. As steps pass, Ratchet ticks off the matching tasks in your spec files. A finished BMAD story is marked done, with a note listing the tests that prove it. When you approve an OpenSpec change, it's archived using OpenSpec's own tool. If there's only one open change, feature or story, you don't even have to name it.

No spec tool? That's fine. The plan you approve is the spec. After your first finished feature, the agent will ask once if you'd like it to keep a simple record of what's been built: one file per feature, with each rule linked to the test that proves it. Yes or no, it remembers your answer.

## CI export

> Ask your agent to set up CI.

That's all you say. Ratchet writes a GitHub Actions workflow that keeps the mechanical checks this run relied on running on every push and pull request: your project's regression or test command, plus every automatic check saved from a guardrail step, in that order.

The AI review gates (adversarial review, UI review and human review) do not run in CI. CI machines don't have the agent CLIs and credentials those gates need, so those stay something a person or an agent runs, not a CI job.

If a hand-written workflow already exists at that path, Ratchet never replaces it. It tells the agent so, and the agent can show you the file instead of writing it.

## Common questions

**How long does it take?** A small feature usually takes tens of minutes, with the agent working by itself. Bigger ones take longer. You don't have to watch.

**Is it expensive?** It uses more AI time than letting the agent just write code, because it checks the work. Ratchet tries to keep that down: low-risk steps get one reviewer, and small nitpicks are noted for you at the end instead of starting another round of review.

**Do I need other AI tools installed?** No. Without them, reviews come from your own agent's company, using a different model when it can. With another company's tool installed and signed in, or with its API key set (Codex, Gemini, Copilot, Cursor, OpenCode, Oh My Pi (omp), Pi and others), that tool reviews first. A second company tends to catch things the first one misses.

**The agent says the hooks aren't working.** Close the CLI, open it again in the same folder, and say "Set up Ratchet and continue". Still not working? Ask it to check that Ratchet's hooks are installed for this CLI and install them if not.

**Where does my code go?** Into your project, like any other change, with one git commit per finished step. If you're on your main branch or have unsaved changes, the agent offers to start a new branch before it begins. If you want one, that's the easiest moment to say yes. Ratchet keeps its own notes in a `.ratchet/` folder.

**Can I stop halfway?** Yes. Say "pause". When you're ready, say "carry on". You can even pick it up in a new chat; the agent just checks with you before moving the run over. Nothing is lost.

**What if I don't like the result?** Say so at the end, the same way you'd tell a colleague. Your feedback turns into new steps, and the agent remembers the lesson for next time. A step added this way (or by hand-editing the plan and confirming with `approve-changes`) is checked and registered the same way either time, so it's built through every gate rather than crashing later.

**What if the reviewers keep finding the same mistake?** The agent will offer to add an automatic check for it, like a lint rule, so it gets caught in seconds from then on. You decide.

## How it works

You don't need any of this to use Ratchet. It's here if you want to know why the gates hold.

### The four gates

| # | Gate | What has to be true | How it's enforced |
|---|---|---|---|
| 1 | **Tests** | Tests written *before* the code all run, and at least one fails on an assertion, then all pass | Ratchet runs the tests itself, checks none were deleted, and reads each planned test's own result, so a test that crashed, was skipped or never ran doesn't count (a step whose tests can't report results one by one is checked by exit code only, and `status` says so). A planned test that already passes before the code exists is not caught by this check. Planned tests are frozen after their first failing run: changing one needs your OK, and so does changing anything a planned test uses (a helper, a `setUp`, a fixture, an import or constant at the top of the file) or a test that was in the file before the run started. A Python test your agent adds during the run (one that is not a planned case and that no planned case uses, even through other code) can be changed or removed freely, with no question (only in a test file that holds a planned case id, and only when no other Python file in the project mentions its name, so a shared helper file stays locked; only the project's own files are checked, with `.gitignore` respected, so a virtualenv is not read; a test that came in from main, through a merge or a fast-forward, even before the plan was approved, was not added by the agent and stays locked); it can't take over the name of a planned test or of anything one uses. That stays true after an approve-changes re-snapshot. Outside Python the limit is plainer: an extra (unplanned) test written before the red run stays frozen with the rest of the file, and a test appended after red is free only until the next approve-changes re-snapshot. This is a structural check, not a proof |
| 2 | **Looks right** | The screen matches the design or the old screen, in the same state | Two separate reviewers compare screenshots and list every difference; any fixable difference blocks |
| 3 | **Review** | Reviewers try to break the code and find nothing serious | Reviewers keep going until none of them has a blocking finding on the current code. A review counts only when the review script signed it for that round, or the host's own reviewer sub-agent wrote it (see below) |
| 4 | **You** | You try it and say yes | Only a real message from you counts |

Work with nothing to look at, like an API change or a refactor, skips gate 2. For rebuilding an old screen, gate 2 compares against the old screen instead of a new design.

### How screens are checked

Tests can tell you a button saves the form. They can't tell you the button is the wrong shade of blue, or that the heading is a bit too small. People notice those things straight away, and nobody ever writes them down in a spec. Comparing screenshots pixel by pixel doesn't work either, because two browsers never draw exactly the same pixels. So Ratchet has a reviewer look at the screen, the way a fussy designer would.

It starts with something to compare against. If you're rebuilding an old screen, that's the old screen. If you've got a design or screenshots, it uses those. If you've got neither, the agent makes a clickable prototype before any real code is written: a single web page in your app's style that you can click through when you review the plan. The prototype follows your design file, `DESIGN.md` (see [What Ratchet sets up for you](#what-ratchet-sets-up-for-you)).

When a step changes something you can see, Ratchet opens both the reference and your real app in a browser, puts them in the same state (same data, same dialog open, same form filled in), clicks through the same few actions on each, and takes screenshots along the way. Then two reviewers look at the results:

- one checks how it **looks**: layout, spacing, colours, text, alignment;
- one checks how it **behaves**: what happens when you click, type, submit or get an error.

They're picked from a different AI company than the one building, if one is available. They only get the screenshots, the page structure and the design file. They never see the code or the chat, so they can't be told "it's fine". They list every difference and where it is on the screen. Any difference that can be fixed in code blocks the step. The agent fixes it, the tests run again, and the screens are compared again, until nothing fixable is left.

Some things keep it honest:

- Each step is only judged on what it built so far. The first step might only be the page header, so the reviewers ignore the table that doesn't exist yet.
- If the two screenshots aren't showing the same thing (say, an empty order next to a full one), the comparison is thrown out and redone. The app isn't changed to match the wrong picture.
- Once a comparison has found problems, the agent can't quietly change what gets compared, or tell the reviewers a difference is "expected". It has to ask you.
- The prototype is never changed to match the app. If you decide the app looks better, that's your call, and the agent will ask.

### Who reviews the work

The model running your agent is called the host. It could be Claude, GPT, Gemini, a Copilot model or something else. Ratchet doesn't care which. What matters is that the reviewer is someone *different*.

So for every review, Ratchet tries these in order:

1. A model from another company, through any AI tool installed on your machine that works (Codex, Gemini, Claude, Copilot, Cursor, OpenCode, Oh My Pi (omp), Pi, or a command you add).
2. A different model from the same company, for example Sonnet reviewing work done by Opus.
3. The same model that did the work, only if nothing else is available.

Each step has a risk level, which you see in the plan. Risk decides how many reviewers look (one for low, two for normal, four for high). It never changes who gets asked first. On low-risk steps, only serious findings block. `normal` is the default risk; a `high`-risk step also has to say which users could be harmed and how if it's gotten wrong (`risk_reason`) — a new plan with a high-risk step that doesn't say so is refused before you ever see it, naming the step.

At the start of each run the agent checks which AI tools are installed and sends each one a tiny test request. A tool that's signed out gets skipped for six hours, and the next one takes its place. A tool that says it is out of credits is different: it waits for the reset time it gave (see below). When a reviewer fails, only that provider is set aside (GLM failing does not take Kimi down, though both run through the one OpenCode program; a program that is not installed does take down everything that runs through it), and its slot goes to another working provider from a different company, and it stays out for the rest of that review round even if its test request later passes (the failure is written into the round the moment the review fails). The replacement is never a reviewer another slot of that round already runs. A slot written with specific reviewers tries the other working providers (same lens) before the host when all of its own are out. When nothing else works, the host's own sub-agents fill the slot so the build never stalls. On a high-risk step that can mean fewer distinct reviewers than four. Substitutions are logged, the plan and viewer say who reviewed each round, and you're told up front if every review will come from the same company that did the build.

What Ratchet learns about a provider is kept once per user, not once per project: the test results and the benches (a bench is "skip this provider until it is due back") live in one file, `provider-health.json`, in Ratchet's per-user folder (`RATCHET_HOME`), and every project and every lane on your machine reads and updates the same one. When one lane finds a provider failing, every other lane skips it too without sending its own test request; when one lane finds it working, the others don't test it again inside the usual re-test window (`probe_ttl_minutes`); when several lanes need the same untested provider at the same moment, one of them sends the test request and the others wait for its answer instead of sending their own, and a late answer never replaces a newer one; when a bench ends, the next lane to need the provider tests it again. Lanes recording results at the same moment all end up saved. Clearing a provider's reviewer with `reviewer-status --reset` clears its bench for every lane. A reviewer your agent marked unavailable by hand (`reviewer-status --mark`) is a per-project choice and stays in that project. If the file is damaged, Ratchet says so and rebuilds it from the next test results. The old per-project `provider-status.json` is ignored, which costs one fresh test request per provider.

When a provider says it is out of credits (Codex's "You've hit your usage limit ... try again at <date>" or "You're out of credits", OpenCode Go's "It will reset in 3 hours 12 minutes"), whether a review or a test request found it, Ratchet reads the message in the provider's own output and benches that provider for every lane (a quota bench) until the reset time it gave, shown as a UTC time such as `2026-10-14T19:04Z` (a clock time with no date means later today; times are read in your local time zone). When the provider gave no time, or one that has already passed, it waits `quota_retest_minutes` (default 20; decimals accepted) and then is tried again. A passing test request does not end a quota bench, since a tiny request can get through when a real review will not; the bench ends at its time, or at once when a real review by that provider succeeds, if that review was sent after the bench began (a review already under way when another lane hit the limit does not end it). Other failures (signed out, an outage) keep the usual `unavailable_ttl_hours` wait. A good review that only quotes the limit message is not mistaken for a quota failure.

Which provider takes a slot, and how reviews are spread. When a slot is `any-<lens>`, Ratchet orders the providers that can review it and tries them in that order. There are two cost groups. Providers that are not per-use come first: flat-rate ones (a subscription already paid for, `"cost": "flat"`) and ones with no cost label (a subscription CLI such as Codex has none). Per-use providers come last, and a per-use provider is only ever in the running when its entry carries `paid_ok_at`, the time of the developer's own exact `pay per use: <model>` reply (lane E's consent, unchanged). A provider marked per-use without `paid_ok_at` is never chosen and is never sent a request, not even a test request, even when nothing else works; the slot goes to the host's own reviewers and the round says why. A flat-rate or unlabeled provider beats a per-use one even when the per-use provider is the one preferred for that lens, and the rule below that likes a company not yet reviewing never lifts a per-use provider over a usable one that is not. Inside the first group the provider that prefers the slot's lens comes first (`providers --add <id> ... --prefer-for correctness`, or a comma list such as `--prefer-for correctness,standards`; the lenses are `correctness`, `standards` and `both`, anything else is refused and nothing is added; stored as `prefer_for` on the provider's entry), then the provider with the fewest recent review starts on this machine, then a flat-rate provider before an unlabeled one, then the one picked longest ago (the time a round last picked it, `picked`; kept when a round replaced before any review gives its starts back, forgotten with a start given back because the provider was never really used), then the order written in config. A model added with `providers --add --preset opencode|omp --model <m>` on a flat-rate service gets `"cost": "flat"`, as discovery gives it; adding a provider id again (for example to set `--prefer-for`) keeps the cost label it already had. A review start is written to the shared `provider-health.json` whenever a round opens, in any project (as `starts`: `{"at", "id"}` entries, the time the round was opened with that provider and the start's id, the round's nonce plus the reviewer id; kept for six hours), so consecutive rounds, and lanes sharing the machine, rotate across the working providers instead of all going to the first one listed. A start is given back when its provider was not really used, and exactly that one by its id, at most once: when `external_review.py` finds the CLI missing, not startable, or exiting with an error when no run of it, the first or the retry, produced any output (the start that round recorded for that reviewer is removed); and when a same-round refill gives a slot to another reviewer or drops it (the start of the reviewer that never ran, including the other reviewers of a provider that failed, swapped out without running). **Fair share across lanes (`provider_share`).** A start is also a reservation of the provider's share. The config setting `provider_share` is `{"max": 3, "minutes": 15}` by default: a provider takes at most 3 new review starts in any 15 minutes, counted across every lane and project on the machine, so lanes that all find a provider working again cannot drain its 5-hour window in minutes. The starts are counted machine-wide, but each project applies its own `provider_share` setting to that count, so keep the setting the same across projects (a project that raises it keeps taking starts after other projects see the provider as full). `max` is a positive whole number and `minutes` a positive number (decimals accepted); anything else is refused with a plain error naming the setting. When a round opens (and when a same-round refill picks a replacement), Ratchet ranks the providers and reserves the starts as one step under the lock of `provider-health.json`, so lanes opening rounds at the same moment can never take more than the share: a provider already at its share is skipped for that slot, the slot goes to the next working provider in the ranked order above (flat-rate first; a per-use provider only with the developer's recorded OK; an explicit reviewer list tries the other working providers before the host), and the round says why in plain words, for example `(instead of codex-both: it already has 3 review starts in the last 15 minutes, and a provider takes at most 3 per 15 minutes across all lanes on this machine)`. Every review slot of a round is one start. Showing a round that is already open again reserves nothing. A provider that is at its share is not sent a test request either. When the window passes, the provider takes new rounds again. A round replaced before anyone reviewed it (the code changed again before any review file was written) gives its reservations back, so replacing a round costs `provider_share` nothing; a round replaced after a review file exists keeps them. A damaged `provider-health.json` is reported on stderr and rebuilt, and the counting starts again from empty. A start is kept when the provider really ran: a timeout, any run that produced output (a reply that could not be read, a non-zero exit after it, a reply cut off twice and then a failed retry) and an out-of-credits failure all keep it. A slot written as explicit reviewer ids keeps the order it was written in; when every provider it lists is out, the other working providers (in the ranked order above, same lens) are tried before the host. The "a company not yet reviewing this round comes first" rule still applies on top of this order, but only among providers of the same cost group.

For OpenCode specifically, discovery goes further on its own: it reads OpenCode's own model list and picks one capable flat-rate model from a company other than yours (never your own company, never a model it can't map to a company, never a small or preview variant), testing it before adding it — no hand-written `providers --add` needed. Oh My Pi (omp) is discovered and labelled the same way as OpenCode: when it's installed and signed in, discovery reads its model catalog (`omp models --json`) the same way and picks a capable flat-rate model (for example GLM through an OpenCode Go subscription reached via omp) before adding it, with no hand-written `providers --add` either. Anything per-use or of unknown cost, from either tool (OpenCode Zen, OpenRouter, an unlisted provider) is never sent a request, not even that test one; it's listed instead, naming the exact tool, provider and model, each one saying it costs money (or that its cost is unknown and so treated as paid) and that you must be asked, by name, before it is ever used — the agent asks you directly, in plain words, before it ever uses one, using the listing's `add_model` id (the full `<provider>/<model>`, not the shorter name shown to you) for both the question and the add that follows it. That question (`ratchet.py ask paid-provider <tool> <model>`) only ever moves forward on one exact reply naming the model, never the plain "yes" that answers every other question; nothing is sent to that model, not even the one-off test request, until you give it, and it only covers that one tool and model. Pi (preset `pi`) has no such model lister yet, so it's listed under the tools that need a model picked by hand, the same as Copilot or Cursor.

Each role also gets its own model. Planning, coding, fixing and reviewing use a strong model. Lighter jobs like planning tests use a cheaper one, and running tests the cheapest. You can change this in `.ratchet/config.json`.

### Later rounds cost less

Round 1 of a step always asks every reviewer. After that, review gets cheaper on purpose:

- **Fewer reviewers after round 1.** When a round blocks and the findings are rebutted rather than fixed, the next one only asks back the reviewer(s) whose kind of review still has an open serious finding; one that approved isn't re-run. (When the fixer has actually run, the fix round below takes over from this narrowing.)
- **No double-counting.** If two review slots would end up at the same reviewer (same company, same model, same kind of review), it only runs once, and you're told the real number that reviewed, not the number of slots configured.
- **A replaced round that nobody reviewed yet is free.** If the code changes again before any reviewer wrote a review, re-opening the round doesn't use up any of your review budget. Once a review has been written, though, that round counts, whether or not it was ever recorded.
- **On a big diff, what changed since the last reviewed round comes first.** When a step's diff is too large to hand a reviewer whole, the files changed since the last round a reviewer actually saw come first; anything still left out after that is named by file, not just a character count, so a reviewer always sees what's new since it last looked, and knows by name what it didn't see.
- **Small issues stop blocking after a couple of rounds.** Serious findings always block, every round. A minor one — a nit, a style note — blocks for the first couple of rounds (`converge_after_round` in config, default 2; so round 3 on), after which it's simply recorded for the final review instead of starting another round over it.
- **A small fix after approval gets one reviewer, covering both lenses.** Once a step's adversarial gate has approved, a code fix at or under `small_edit_lines` (default 20 changed lines, measured since the last full multi-reviewer approval so several small fixes in a row still add up to a full round) opens its next round with a single reviewer who checks both standards and correctness, instead of the step's whole reviewer set — unless the step is high risk, which always keeps the full round. The commit message and `status` say when a round was reviewed this way, and by whom.
- **After a block, the fix round gets one reviewer.** Once a round blocked and the fixer has run, the next round is a fix round: it opens with exactly one reviewer, whatever the fix's size and whatever the step's risk. That one reviewer is the one whose blocking finding was fixed, with the same provider and the same lens (kind of review), so a Codex correctness finding is re-checked by Codex correctness; when several reviewers blocked, one from a company other than the host's is preferred, and then the lowest slot number. Any finding that actually blocked that round counts, whatever its severity, so a round that blocked on minor findings alone (at normal or high risk, where every severity blocks) starts a fix round too. If that reviewer is unavailable (benched, out of quota, or already at its share of review starts), its slot's own stand-in for the same lens takes the round, and no provider that had not reviewed the step is ever brought in. A fix round counts toward the step's and the run's review limits like any other round, and when it blocks again it goes to the usual check-in before another round can open. `status` and the commit message name each fix round and the one reviewer that re-checked it.

- **A fix round's reviewer sees only the fix.** In a fix round (after a block, or the small fix after an approval above), the reviewer is shown only the fixer's diff, meaning what changed since the last round a reviewer actually saw, plus the open findings the fix was meant to fix (id, severity, file, line, description, suggested fix, and the rebuttal reason if the fixer argued and lost); a finding you accepted is not listed. A small fix after an approval has no findings to list. Round 1, a fix over `small_edit_lines` and a high-risk step's fix keep the whole step's diff. A host sub-agent and another company's CLI get the same scope. If a fix is too big to show whole, the prompt names the files it left out and a review of it is refused rather than counted, whatever it found. Only a fix round gets a shorter minimum review time, one that scales with the size of its diff and never exceeds `min_review_seconds` (default 20 seconds), so a review of a 2-line fix that comes back in about 10 seconds is accepted; a full round keeps `min_review_seconds` as configured.

### One flaky failure doesn't cost you anything

Tests and regression suites occasionally fail for reasons that have nothing to do with the code: a slow machine, a timing hiccup, a test that touches the network. If the test command (or, at `complete`, the full regression suite) fails once and then passes on one immediate automatic re-run, Ratchet marks it **flaky** and lets it through without spending an attempt — once per step for the tests, and once for the whole run for the regression suite. A second flaky-looking failure, or two failures in a row, is never forgiven: it counts as a real failure like always. `status`, the evidence folder (both the failing and the passing log are kept) and the final review all say plainly which steps passed only after a flaky retry.

**Run `complete` in the background.** `complete` runs the whole regression suite, which can take up to `test_timeout_sec` (15 minutes by default) per run, up to `load_stretch_max` times that on a busy machine (below), and after a failure it may run the suite a second time, so up to about twice that; an agent's tool timeout can cut a foreground call off partway. When `complete` is not noted as a background call, it prints a warning on stderr first, before any slow work, that a tool timeout could cut it off and that it should be run in the background instead; the warning never changes what `complete` does or its exit status.

**A busy machine gets more time, not a failed step.** Every command Ratchet runs for a step (the step's tests at `red` and `behavior`, the automatic checks, a guardrail step's check runs, the regression suite at `complete`) has the time limit `test_timeout_sec`, and on a busy machine that limit stretches: it becomes `test_timeout_sec` times the machine's one-minute load average divided by its CPU count, re-read every quarter second while the command runs (so a load spike in the middle still extends it, and a load drop never takes back time already granted: the highest limit reached stays in force), never less than `test_timeout_sec` and never more than `load_stretch_max` times it (default 4; `1` turns the stretch off). On an idle machine, or one with no load reading, every limit is exactly the configured one. When a command ran past `test_timeout_sec` or was stopped at a stretched limit, its saved log says so in its header (`time limit stretched 2.0x (900s to 1800s) at load 16.0 on 8 CPUs`, the highest limit reached). A command still running at the stretched limit is stopped with everything it started and logged as `TIMEOUT after <limit>s`. When ratchet.py itself is stopped while a command runs (a tool timeout cutting a foreground call off), it stops the command and everything it started first, so no suite keeps running behind it. A stretched pass counts as a plain pass: it spends no flaky re-run.

**An edit while the tests run stops the run, and the run does not count.** While Ratchet runs a step's tests (`behavior`: the step's tests, their automatic re-run and the automatic checks; `complete`: the related pass, the full suite and its automatic re-run), it looks every `suite_watch_seconds` (default 30 seconds; any number above 0, fractions included; locked at plan approval) at whether the code changed under them, and once more when the command ends on its own, so a quick suite that finishes before the next look never counts either. A change is a file the run started with being edited or deleted, or a new project source file appearing (any file git would commit, a template, a stylesheet, a config file or a module in a folder called `reports/` included, that is not generated output and not a doc: outside every build, dependency, cache, coverage, snapshot, log, temp and scratch folder and every dot folder, not a generated name such as `*.min.js`, `*.map`, `*.log`, `coverage*` or `junit*.xml`, and not a `.md`, `.rst`, `.adoc` or `.txt` file). Ratchet's own `.ratchet/` folder, the agent's chat transcript (even when it lives inside the project), ignored files and any other file that only appeared (build output, a cache, a snapshot, a report, a log or scratch output the suite itself writes) are not changes. When the code changed, Ratchet stops the command and everything it started (politely first, then by force), writes the run's log as `stopped: code changed`, frees its lock, and says plainly that the code changed and which command to run again. A stopped run is never a pass, never a failed attempt and never a spent flaky re-run: the gate's status, its attempt count, the flaky allowances and the step are exactly as before the call, nothing is committed, and the next command in that project runs at once. A run during which nothing changes finishes exactly as before; a command that really hangs is still stopped at its time limit and counts as before: a timeout always counts, as one failed attempt, even when a file also changed after the last look (a command killed by its time limit gets no last look). The looks are cheap: between looks Ratchet keeps one private git index, so a look rehashes only the files whose stat changed (0.05 s on a 30,000-file tree, against 1.8 s for a fresh fingerprint), and a look git cannot take (a file the suite created and removed under git's walk, or one it cannot read) is skipped and noted in the run's log, never a crash with the suite still running; the one last look when the command ends is a full fingerprint. A project that sits in a subfolder of its repository is watched the same way (file names are relative to the project). **A change found only when the command ended** (the command ran to its end on its own, and the last look finds a file it started with changed) is not a stop: Ratchet cannot tell whether the command itself wrote the file (a regenerated snapshot, a coverage or JUnit report that is tracked, a formatter) or it was edited while the command ran, so it refuses the run naming both causes, with the right guidance for each file: an edit you made yourself is kept; a file the command wrote, tracked or not, is put back as it was when the run began, with a git command the message gives that restores from the tree the run started on (your uncommitted work and your new files included, never HEAD), and the command is fixed or the file is taken out of the fingerprint (`.gitignore`, and out of git's index first if git tracks it). Nothing the run began with is ever deleted. A new source file found when the command ended gets its own guidance, since it was not there when the run began: keep it if you added it; if the command wrote it, remove it or add it to `.gitignore`, and stop the command writing source files into the project. That refusal counts nothing either, however often it repeats: never a pass, never a failed attempt, no flaky re-run spent, and at `complete` nothing committed and no gate touched. Ratchet remembers each mid-run stop next to the run's logs; when the very next run of the same command is stopped on the same files, the message adds that if you did not edit them the command itself writes them, with the same guidance. A run that ends unstopped clears the record for its command.

**Related tests run first, so a break shows in seconds.** When `related_test_command` is configured (a shell command with a `{files}` placeholder; a project turns the pass on by setting it, for example `python3 tests/run_all.py {files}` for Ratchet's own suite runner), `complete` runs a few test files before the full suite: the step's own planned test file(s) (`planned test`), the test files the step changed (`changed test`), and the test files that name a changed file by its path or file name, or import it (a Python module's dotted path such as `app.billing` or `from app import billing`; a JS/TS module's extension-less path such as `src/billing` or `'../billing'`; a file the step deleted counts too), shown as `names src/x.js`, the ones that mention a function the step changed first. It prints one line per file with its reason. The list is capped at `related_tests_max` files (default 8; a value that is not a whole number of 1 or more is ignored and 8 is used, and the output says so), so a change to one big shared file still gives a short list, never the whole suite. If one of them fails, the step stops right there, names the failing file, and the full suite is not started; that is not a suite failure, so it spends no flaky re-run and no gate changes. When they pass, the full suite still runs afterwards and must still pass, so the cap never drops coverage. The files in `related_always` (a list of test files, empty by default; for Ratchet's own repo the value to set is `["tests/test_docs.py"]`) join the list outside the cap, labelled `doc check: <trigger>`, whenever the step adds a new test file, changes a doc file (`doc file changed`), or adds or changes a code line holding a quoted string (`user-facing text`); a file listed there that does not exist is skipped with a note. With `related_test_command` unset the step says the related pass is off and goes straight to the full suite (and `plan-summary` says so up front); a value without `{files}` is refused by `config --set` and by `complete`, naming the key. The app scout sets `related_test_command` at setup when it finds a test runner that takes file arguments. Both keys and `related_always` are locked at plan approval like `regression_command`: changing them afterwards needs `approve-changes`. The related run's output is kept at `evidence/CPn/related-tests.log`.

The same idea applies to another company's reviewer: if its reply is cut off mid-answer (an incomplete JSON object, not a real error), that reviewer is retried once before anything is held against it. If the retried call is cut off again too, only that reviewer's own slot falls back to the next alternative for this round — the provider isn't sidelined for hours the way a genuine quota, login or timeout failure is, and any review another reviewer already finished in the round is kept untouched. A reviewer that is really unavailable (not installed, signed out, out of quota, or timed out) is still skipped exactly as before, with no retry wasted on it.

**OpenCode and big prompts.** OpenCode (GLM, Kimi) is given the review prompt on stdin, after a short message, so a prompt of any size reaches it whole. It also runs with `--format json`, a fixed session title (`--title`, which stops OpenCode from sending the whole prompt a second time, in the background, just to name the session) and `--variant low` by default, which asks for low thinking: OpenCode applies a variant only to models that define it and silently ignores it for the others (for example GLM, except glm-5.2, and Kimi, where it changes nothing). Another level for one provider is set with `providers --add <id> --preset opencode --model <m> --variant high`, or `--variant none` sends no variant at all (adding the provider again keeps its level). Ratchet reads the reviewer's answer from the text events of the JSON stream only, never from reasoning or tool events, which can echo the request or hold example findings. An answer with no text (exit 0 and an empty answer), or one that stopped with the finish reason `length` (the model ran out of output room, even if what it wrote looks complete), is a failed attempt, never an approval and never a clean pass: only that slot goes to another provider for this round, the message says no answer text or cut off, and the provider is not benched. A `length` finish is a failed attempt even when an error event comes with it, and so are an empty answer or a reply in prose with no findings JSON when the run itself finished cleanly (exit 0, no error event); none of them benches the provider. Any other error event is a provider failure, benched as usual. A usage-limit message in an error event or on stderr benches the provider for every lane, as described above; the answer text is never searched for one, so a reply that quotes a limit message does not bench it. A prompt too large for a command line (a CLI that takes the prompt as an argument: Copilot, Cursor, omp and pi, or your own command with `{prompt}`) never benches or marks the provider, in any lane: that slot moves on for this round, the review start is given back, and the provider's next review is sent as normal.

### Staying aimed at real users

Every reviewer's prompt, and every builder, fixer and test-writer's brief, opens with the plan's objective: who the project's users are, what they want, what done means, what's out of scope. Reviewers judge every finding against that objective, not against some other project's users, so a finding has to describe real harm to this project's users in realistic use, and severity follows real user harm in normal use. What's out of scope comes from the objective's own `out_of_scope`, and it differs by project: Ratchet's own plans list deliberately evading one of Ratchet's own checks as out of scope and, at most, a minor note (an accepted same-user-forgery limit), but a public-facing project's plan can and should treat malicious users as in scope. A reviewer has to say who is hurt and how (`user_impact`) next to a finding's severity; a blocker, major or unrecognized-severity finding with no `user_impact` never blocks, at any round and any risk, and is recorded instead for the final review (`ratchet.py deferred`). A run whose plan has no stated objective (one approved before this existed) still reviews normally, falling back to the project's own standards.md for who the users are and what's out of scope, with a plain note that none was given.

### When reviews drag on

Reviewers can go back and forth for a while on a hard step. Ratchet itself watches for that, not the agent: once a step blocks a second round in a row, or a kind of finding the agent hadn't seen yet shows up a second time in a file an earlier round already flagged, it pauses before another round and makes the agent check in against the plan's own goal. The agent never decides this alone: it spawns a fresh strong-model reviewer with the objective, the open findings and the round history, which recommends narrowing or changing the step (simplify), asking you to accept the open findings as a known limit (only your own reply does that; the agent can't wave them through itself), or stopping to ask you how to wrap up. Separately, once a round blocks, the agent has to decide fix or rebut for every remaining open finding, however many, with a one-line reason, before it can open another round; a disputed finding it chose to rebut is shown to the next round's reviewers, who either drop it or press it again with a clearer case for who it would actually hurt. The final review, when everything is built, opens the same way: your goal, who it's for, and each thing "done" was supposed to mean, listed plainly for you to judge against the evidence, not claimed as met just because the checkpoints finished.

### Pasting a proof receipt into the pull request

Once you say yes to the final review's approval question, the agent runs `human-approve` and then runs `ratchet.py receipt`, pasting its finished markdown straight into that same wrap-up message (you never run `ratchet.py` yourself): one honest receipt to paste into the pull request. Each step's title, short commit and elapsed time, plus the whole run's own elapsed time: wall-clock, including any time spent waiting on you, never cost or tokens. Who reviewed each step and its rounds, with a review by the host's own sub-agent marked as such and why an outside reviewer was skipped, when Ratchet recorded that reason (otherwise it says plainly that the reason wasn't recorded). The findings you accepted, with your reason; the ones still left for later; and the ones `mark-fixed` closed later in the run. A step with no commit says so plainly ("no commit recorded") instead of guessing; because the receipt is only pasted after approval, its run total is always the finished total, never a time-so-far guess. The receipt also lists every line of the plan's objective ("done means") next to the test case(s) that prove it — id, title, file and whether it passed — so a line with no case naming it reads plainly "no test linked" instead of a made-up pass. `ratchet.py receipt --json` prints the same facts as data, for anything that wants to read them.

### Mistakes that keep coming back

Reviewers label every finding with the kind of mistake it is. When the same kind shows up a second time, the agent offers to turn it into an automatic check, like a lint rule or an architecture test. Say yes and the check is built as its own small step, through every gate. Before it counts, Ratchet runs it against a deliberately bad example and against the real code where the mistake was found. It has to catch both and pass on today's code. For high-risk mistakes (security, data, money), a separate writer who never sees the check first writes disguised versions of the mistake, and the check has to catch those too. If a reviewer later finds a case the check missed, Ratchet strengthens it automatically and tells you.

### Why the agent can't cheat

- **Ratchet checks, it doesn't take anyone's word.** The agent doesn't get to say "tests pass". Ratchet runs them. Reviewers don't get to say "approved". Ratchet works out the verdict from what they found.
- **Tests come first.** A test that passes before the feature exists is rejected, because it isn't testing anything.
- **Tests can't disappear.** Every planned test has to still be there each time the tests run.
- **Any change resets the gates.** Every gate result is tied to the exact code it checked. Change the code afterwards and the gates run again.
- **Same state, or it doesn't count.** If two screenshots being compared aren't showing the same thing, the comparison is thrown out and redone.
- **Isolated UI reviewers.** The provider comes from `ui_reviewers` in the config: any supported tool other than the host, with the host's own sub-agents as the fallback. They work in a temp folder that holds only the screenshots, accessibility trees and the design file. They never see your project's agent instructions, the code or the chat.
- **Protected records.** A hook blocks anyone from editing Ratchet's records by hand. In Claude Code, which tells Ratchet which sub-agent is acting, it goes further: only the right reviewer can write each review, and during the build the agent in charge can't edit your code itself. Only the sub-agent doing that job can.
- **No quitting early.** Another hook stops the agent from ending its turn until a gate is passed, unless it's waiting for you, stuck, or paused.

### Using it with Codex, Gemini or Copilot

Claude Code finds Ratchet's skills on its own. For the non-Claude CLIs (Codex, Gemini CLI and Copilot CLI), the installer puts one shared copy in `~/.ratchet-loop/skills` (or `$RATCHET_HOME/skills` if you set that). Your CLI needs to be able to see it, for example with the skill folders linked into its own skills folder. The agent can do this for you. Just ask it to "link the Ratchet skills into this CLI's skills folder".

The four CLIs do not all enforce the same things. Here is what each one actually does today:

| CLI | Protects Ratchet's records | Confirms reviewer identity | Stop hook | Records your replies |
|---|---|---|---|---|
| Claude Code | Yes, Ratchet's state and evidence files are protected from hand-edits. | Yes, Claude Code tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |
| Codex | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |
| Gemini | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent. |
| Copilot | Yes, Ratchet's state and evidence files are protected from hand-edits. | No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer identity. | Yes, the stop hook stops the agent from ending its turn early. | Copilot's prompt hook drops its own message, so the agent never sees Ratchet's guidance, but the reply is still recorded in Ratchet's log. |

Only Claude Code tells Ratchet which sub-agent is acting; none of the other three can do that today, so Ratchet cannot confirm reviewer identity on Codex, Gemini or Copilot the way it can on Claude Code.

Review checks, on all four CLIs: a review from another provider counts only as a script-signed review. `external_review.py` (and `ui_review.py` for the screen reviews, which also signs the capture manifest the reviewers judged) signs what it writes with a secret made for that one round, kept in Ratchet's per-user folder, never printed, never in the project, and deleted once the round is recorded. A review from another provider that was typed, copied or edited by hand, or signed for another round or step, is refused as unsigned. A review by the host's own reviewer sub-agent is not signed, and how much Ratchet can check it depends on the CLI. Only Claude Code confirms which sub-agent wrote it: there the review counts only when Ratchet's guard hook saw that reviewer sub-agent write it with the Write tool, and a review written by the main session, through the shell, or by hand is refused as unsigned. On Codex, Gemini and Copilot, or when you waived the hooks, Ratchet cannot tell who wrote a host reviewer's file: any file in the round's folder with the right round nonce and code version counts, even one typed by hand, so the run never stalls, but `status` (and the viewer, for code reviews) marks it "not independently confirmed". Run outside a Ratchet run, `ui_review.py` still compares screens for an ad-hoc check, but its results are marked unsigned and never count for a gate. The secret protects against a different process on the same machine, not against the operating system: any program running as your own user can read Ratchet's per-user folder, and only the guard keeps the agent out of it.

On all four, the guard also refuses commands that set Ratchet's reserved variables (`RATCHET_HUMAN_OVERRIDE`, `RATCHET_ROLE`, `RATCHET_HOME`, `RATCHET_DIRECTOR_KEY_FILE`), including indirect ways such as `declare`, `eval` or a `python3 -c` that writes `os.environ`, and it still refuses a shell command that calls Ratchet's own hook command itself, directly or through a wrapper. A command that only mentions one of these reserved names or the hook command, in a note, a brief, a heredoc saved to a file and never run, or a quoted `echo`/`printf`/`git commit` argument, is allowed; only actually setting the variable or calling the hook is refused. Ratchet itself also refuses a per-user folder or key file the hooks don't use, and the human override only works after a person types back a code shown in a real terminal. This is best-effort against an agent that can run anything: one that writes a script to a file and runs it later, or starts its own pseudo-terminal to read the code, can get further than the guard sees. Put plainly: these protections stop an agent from stumbling past a gate or taking an easy shortcut, but a script deliberately written to cheat, running as your own user, can still forge Ratchet's records, for example by calling Ratchet's own hook command directly with made-up input.

Read-only commands on `.ratchet/` run files, including after `cd`, in loops and pipelines, are allowed, but only a short list of forms the guard can prove write nothing: every plain reader (`cat`/`head`/`tail`/`wc`/`ls`/`stat`/`grep`/`egrep`/`fgrep`/`rg`/`less`/`more`/`file`/`cmp`/`md5sum`/`sha1sum`/`sha256sum`/`tree`/`du`/`cut`/`tr`/`diff`/`jq`/`echo`/`printf`/`pwd`/`read`/`basename`/`dirname`/`realpath`), `cd`/`pushd`/`popd`, `sort` without `-o`/`--output`, `uniq` with at most one file, `find` without `-delete`/`-exec`/`-execdir`/`-ok*`/`-fprint*`/`-fls` (and, when it does use `-exec`/`-execdir`, only to run one of these same plain readers), `xargs` running one of these same plain readers (so `xargs rg` passes exactly like `xargs grep`), `sed -n` (optionally with `-E`/`-r`) with one print-only address script (`'1,5p'`, `'$p'`, `'/x/,/y/p'`; nothing with `-i`, `w`, `W`, `e` or `r`), `awk` with only an optional `-F<sep>` and a script with no redirect, pipe, `system`, `getline`, `close` or `fflush`, and `cp` with no options and a literal, absolute destination outside `.ratchet/`. A `|`, `;` or `&` that sits inside one of these forms' own quotes (a `jq` filter, a `grep -E` alternation, an `awk -F'|'` separator) is data, not a statement boundary, so it doesn't split the command apart. This holds after `cd`-ing into `.ratchet/`, inside a `for`/`while` loop, or piped together. Writing a file anywhere outside `.ratchet/` with `echo` or `printf` is allowed even when its own quoted text names a run path, for example `echo 'see .ratchet/runs/x/state.json' > /tmp/note.txt`, as long as any redirect that command actually writes to resolves outside `.ratchet/`; a `git commit`/`tag`/`notes`/`merge` message is treated the same way, for every `-m`/`--message` it has (a subject and a body, say) and for a combined short flag ending in `m` such as `-am`. Everything else that names a run path is refused, even just to read it: an interpreter other than the python reads described next (`node -e`, a python script file, any other `python3 -c`/`-m` form), an archiver, `sort -o`, `uniq` given a `-` or two files, `awk -i`/`-f`/`-v`, any other `sed`/`cp` form, `cp`/`mv`/`tee` writing to a path named only in their own argument, and a relative redirect or `cp` destination in a command that also `cd`s to a target this guard can't resolve (a variable, a substitution, a bare `-`, or an option): that target is treated as possibly still inside `.ratchet/`, the same as one that names `.ratchet` outright. If the agent's shell is already sitting inside `.ratchet/` (for example after a `cd` in an earlier call), only those same plain reads are allowed from there; anything else is refused and told to `cd` back to the project root first. What is still refused everywhere: redirecting into a run file, in every operator form (`>`, `>>`, `N>`, `N>>`, `&>`, `&>>`, `>|`, `N>|`, `>&word`, with a plain fd duplication such as `2>&1`/`>&2` and `/dev/null`/`/dev/stdout`/`/dev/stderr` the only exceptions); a `>(...)` process substitution whose own inner program (`tee <file>`, say) writes into one, whatever the outer command reading into it looks like; `cp`/`mv`/`tee`/`touch`/`truncate`/`rm` on one; `sed -i`/`perl -i` editing one; `find -delete`/`-exec` acting on one; `uniq` given a `-` or a second file as its own write target (a bare `uniq <one file>`, with no `-`, still just reads it); a `find ... | xargs rm` whose files come from inside `.ratchet/`; and a `while`/`until`/`for VAR in LIST` loop (its own `do` may follow a `;` or a plain newline) whose input (a pipe from an earlier stage that names a run path, or a `< file`/`< <(...)` redirect that does), list, or `cd` state is inside `.ratchet/` -- which means only: the session's own cwd is inside `.ratchet/`, or the command carries a `cd`/`pushd` whose literal target names `.ratchet/`, or one whose target can't be resolved at all (a variable, a substitution, or a bare `-`); an ordinary `cd` to a plain, unrelated relative path such as `cd docs` does not count -- and whose body (checking every pipeline stage on its own, not only its first stage before a `|`) writes anything at all is refused too. Check-in decision (CP4 round 2): the earlier rule only flagged a write whose source or destination happened to be the loop's own variable, which kept leaking narrower cases every review round, so there is no per-argument exception any more -- a stage writes when it carries a `>(...)` process substitution outside quotes (`cat /tmp/new.json > >(tee "$f")`, whatever program sits inside the parens), an output redirect target that isn't a literal, absolute path outside `.ratchet/` with no `$`, or when its program is `rm`/`rmdir`/`touch`/`truncate`/`tee`/`chmod`/`chown`/`cp`/`mv`/`ln`/`install`/`dd`/`sponge` (any of these, whatever its own arguments), `sed`/`perl` with `-i`/`--in-place`, `awk`/`gawk` with `-i`, `python`/`node` holding a write marker, or `uniq` given a `-` or two operands. A loop whose body writes this way, whatever the writer or variable, is always refused, whether the loop is a `while`/`until read` or a `for`; a loop body stage that only redirects to a literal absolute path outside `.ratchet/` (`cat "$f" > /tmp/copy.json`, `jq .phase "$f" >> /tmp/summary.txt`) or carries none of these writer programs at all (`wc -l < "$f"`, `echo "== $f"`, a `cat "$f" | jq .phase` pipeline) stays allowed, as does a loop reached through an ordinary relative `cd` into an unrelated project folder. To copy or summarise a run file in a loop, redirect each stage to a literal path outside `.ratchet/` like that instead.

Two more rules this guard applies. A heredoc counts as code, and its body is checked for a run-path write, when the interpreter it is fed to appears either before the heredoc operator on the opening line (`python3 <<EOF`) or after it, piped onward (`cat <<'EOF' | python3`, `cat <<'EOF' | bash`); a heredoc fed only to a plain reader such as `cat`, or written straight to a file, is data, not code, even when its own body merely mentions a run path or an interpreter name in prose. Inside a loop, quoted text is never read as a command: a writer-program word, a redirect, or the loop's own closing `done` sitting inside the body's own quoted string (a grep pattern, a jq filter, an echoed note) never counts as a write, or as the loop's end. When a live `$(...)`/backtick substitution inside a double-quoted span keeps the exact quote boundaries from being known, the loop check falls back to a coarser mask that blanks every quoted span whole, rather than guessing from raw, unmasked text. A command naming Ratchet's run folder (a literal `.ratchet` path segment anywhere in its raw text) that also contains `$(` or a backtick anywhere is refused outright before this loop check or the read-only fast path ever runs, with no parsing of what the substitution does (single-quoted text and quoted-delimiter heredoc bodies are not executed, so they don't count): run the substitution as its own command first, then use its result in a separate command. A command that never names the run folder is untouched by this rule.

Three python forms read run files too, whether or not the command `cd`s first and even when a run path is named directly with no `cd` at all: `python3 -c '<code>'` with exactly one code argument and no option besides `-B`/`-E`/`-I`/`-s`/`-u`; a bare `python3` or `python3 -` fed a heredoc; and `python3 -m json.tool` given at most one input file and no output-file argument (so `python3 -m json.tool state.json state.json`, an in-place rewrite, is still refused). Each is read-only only when its code or heredoc body holds none of: an `open(`/`.open(`/`io.open(`/`codecs.open(`/`os.open(`/`os.fdopen(` call appearing anywhere in the same code alongside a quoted string literal (optionally `b`/`r`/`f`-prefixed) whose whole content is made only of the characters `r`, `w`, `a`, `x`, `b`, `t`, `+` and holds a `w`, `a`, `x` or `+` -- checked over the whole code, not tied to that call's own argument position, so a mode merely assigned to a variable counts too, fail-safe -- or a `mode=` keyword followed by anything other than a quote; `.write(`, `.writelines(`, `write_text(`, `write_bytes(`, `json.dump(`, `pickle.dump`; `os.remove`/`unlink`/`rename`/`replace`/`rmdir`/`removedirs`/`makedirs`/`mkdir`/`truncate`/`system`/`popen`/`exec`/`spawn`/`fdopen`; `shutil.`; `subprocess`; `.touch(`, `.unlink(`, `.rename(`, `.replace(`, `.mkdir(`, `.rmdir(`, `.symlink_to(`, `.hardlink_to(`, `.writerow(`, `.writerows(`; `exec(`, `eval(`, `__import__`, `importlib`, `getattr(os, `; reassigning `sys.stdout`; `file=` (as in `print(data, file=f)`); `fileinput`; `inplace=`; `os.chmod`/`.chmod(`; `os.symlink(`/`os.link(`; or an `O_WRONLY`/`O_RDWR`/`O_TRUNC`/`O_CREAT`/`O_APPEND` flag. A `from os/shutil/subprocess/pathlib/io/fileinput/tempfile import ...` clause counts as a write too, except `from os import path`, `from os.path import ...` (any names), `from pathlib import Path` alone, and `from pathlib import PurePath` alone, which stay reads; an aliased `import os`/`shutil`/`subprocess as ...` counts as a write, and so does a bare `import shutil` or `import subprocess` with no alias (plain `import os` used as `os.path...` stays a read). Any of those markers, or any python form this can't read this precisely (another module, extra options, two `json.tool` file arguments, an unreadable quote), is refused exactly as it was before this check existed.

If the guard or prompt hook breaks, it lets the action through and writes the error to `.ratchet/hook-errors.log`, rather than blocking everything, because a crashing guard must never brick a session. If the stop hook breaks while a run is active, it fails closed instead: it blocks the agent from ending its turn and tells it to explain the failure to you, for up to five tries in a row; after that it lets the agent stop too, logged the same way, so a broken install can't trap the session forever. For the next 24 hours, `status` says in plain words that a safety hook failed and what to do, and the agent sees the same errors in `next`. Until that hook runs cleanly again, Ratchet does not count it as working.

**Waiting on background work.** The stop hook normally refuses to let the agent end its turn while a gate is open, and after too many refused stops in a row without progress (`max_stop_blocks`) it halts the run for you to look at. When the agent has started reviewers or sub-agents in the background and has nothing to do until they report back, it runs `ratchet.py wait --for "<what it is waiting on>" --minutes N` first. Until that time (N is kept between 1 and 120 minutes; 30 if not given) the stop hook lets the run's own chat end its turn, and those stops are not counted as a blocked stop, so waiting never trips the safety valve. Only the chat the run belongs to can wait, and Ratchet tells which chat that is from what its own hooks saw, never from an id the agent passes in. The wait ends early as soon as a gate makes progress, or the run is paused, resumed or cancelled; after that, or once the time is up, stopping is blocked again as before. `status` and `next` show what the run is waiting on and until when. A wait lets the agent stop no more than `pause` already does; it just keeps the run going instead of pausing it.

**A command that was cut off.** Every command that changes a run holds one lock file, `state.lock` in Ratchet's folder, while it works, so two commands never change the run at once. If an agent's command is killed part-way (a tool timeout, say), it leaves that lock behind. On Linux, a lock left by a command whose process is no longer running on this machine (same boot, same process namespace) is cleared at once by the next command, and the run carries on; a lock held by a command that is still running is respected, and the agent is told Ratchet is busy and to try again when it finishes. A lock Ratchet cannot check (one in an older lock file format, one written on another machine or from inside another sandbox or container, a command run through `python -c` or `python -m` or a copy of ratchet.py under another name, or any lock on macOS, Windows or another system than Linux) still clears by the timed rule: after the longest test run plus a margin, a little over an hour by default, since its command last refreshed it. The agent never has to remove the lock by hand.

**Waiting for an answer.** When the agent has asked you (or, in a lane run by a director, the director) a question and no answer has come yet, `status` says so in one extra line: it is waiting for the answer, names the kind of question in plain words and shows when it was asked, so a waiting lane is never mistaken for one that is still building. The line only says the run will not move for the questions that really hold it up (the plan and step approvals, and the questions of a blocked run or review round); for the others (how often to review, cancelling, the hooks question, a choice while building) it says the run can carry on meanwhile. `status --json` and `next` carry the same facts as a `question` entry with `kind` (the kind of question, for example `approve-plan`), `since` (when it was asked), `waiting_on` (`user` or `director`; a claim is always `user`, because a director cannot sign it) and `stops_run`; the entry is null while nothing is waiting, and again as soon as the user has replied (even before the agent acts on the reply; a reply never drops it in a director lane, where only the director's signature clears it) or the question is withdrawn. A question that was printed as NOT ASKED (it replaced another question still waiting for its first reply, so it never reached the user) is not reported, and neither is the one it replaced. Reading it changes nothing, and it still works when the director enrollment file is damaged.

**Full requirements:** Python 3.8+, git, and Node 18+ with Playwright for checking screens. Other AI tools are optional (`claude`, `codex`, `gemini`, `copilot` and others); without them, your agent's own sub-agents do every review.

### Objective check on sub-agent launches

Launching a Ratchet sub-agent during a build without the plan's objective in its prompt is only enforced (refused) on Claude Code, because it's the only one of the four CLIs whose hooks can see a sub-agent launch at all.

| CLI | Objective check on sub-agent launches |
|---|---|
| Claude Code | Yes, enforced: a Ratchet sub-agent launched during a build without the plan's objective in its prompt is refused. |
| Codex | No, not enforced: the objective check does not run because Codex cannot see a sub-agent launch. |
| Gemini | No, not enforced: the objective check does not run because Gemini cannot see a sub-agent launch. |
| Copilot | No, not enforced: the objective check does not run because Copilot cannot see a sub-agent launch. |

On Codex, Gemini and Copilot, the agent should still start every sub-agent's prompt with the output of `$RATCHET brief`; nothing checks it there.

## Reference (commands the agent runs)

Everything above is done by talking to your agent. This section is for people typing at a terminal, and it lists what the agent runs on your behalf. `ratchet.py` lives in `skills/ratchet-orchestrator/scripts/`.

### What's inside

```
ratchet-loop/
├── .claude-plugin/plugin.json       Claude Code manifest
├── .codex-plugin/plugin.json        Codex manifest (same name and version)
├── hooks/hooks.json                 Stop, UserPromptSubmit, PreToolUse
├── install.sh                       installer for every supported CLI
├── skills/
│   ├── ratchet-orchestrator/          the one skill you prompt
│   │   ├── SKILL.md
│   │   ├── scripts/ratchet.py         state engine: checkpoints, gates, fingerprints, evidence, viewer, hooks
│   │   ├── scripts/spec_sources.py    reads OpenSpec, Spec Kit and BMAD files; ticks tasks back
│   │   ├── scripts/provider_health.py the shared provider test results and benches (provider-health.json)
│   │   ├── assets/viewer.html         checkpoint viewer (plan review + live progress)
│   │   └── references/schemas.md      every file format
│   ├── ratchet-tdd-planner/           plan tests before code
│   ├── ratchet-design-md/             create/extract DESIGN.md
│   ├── ratchet-prototype/             single-file clickable HTML prototype
│   ├── ratchet-ui-compare/            Gate 2: screenshots + isolated reviewers
│   └── ratchet-adversarial-loop/      Gate 3: adversary and fixer loop
├── agents/                          reviewer and worker prompts
└── tests/                           standalone test scripts
```

### Installing by hand

```
./install.sh [--host claude|codex|gemini|copilot|all] [--project]
```

| Form | What it does |
|---|---|
| `./install.sh` (same as `--host auto`) | installs for the agent CLI running the script, detected from its environment; Claude Code if it can't tell |
| `--host claude` | Claude copy in `~/.claude` (skills and agents) plus Claude hooks |
| `--host codex` / `gemini` / `copilot` | shared copy in `~/.ratchet-loop` (or `$RATCHET_HOME`) plus that CLI's hooks; the skills are also linked into `~/.codex/skills`, `~/.gemini/skills` or `~/.copilot/skills` when that folder's CLI is set up |
| `--host all` | the Claude copy, the shared copy, and hooks for all four CLIs |
| `--project` | Claude's copy and every CLI's hook config go into the current repo (`.claude/`, `.codex/`, `.gemini/`, `.github/hooks/`) instead of your home folder. The shared skills copy for Codex, Gemini and Copilot still goes to `~/.ratchet-loop` (or `$RATCHET_HOME`), and the project hook files point at it |

It is safe to run twice: skill folders are replaced, hooks are not duplicated, and your own hooks and settings are left alone. Claude Code and Codex can also load the repo as a plugin through their manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`). Hooks only: `ratchet.py install-hooks --host <cli> [--scope project|user] [--dry-run] [--uninstall]`.

### Commands

| Command | Purpose |
|---|---|
| `ratchet.py doctor [--json]` | which CLIs have Ratchet hooks and when each last fired |
| `ratchet.py status [--json]`, `plan-summary` | plain-text state; the plan step by step |
| `ratchet.py changes CPn [--json]` | read-only: which changes since the step started are docs, which are code and how many code lines changed, which test files name each changed doc, and whether `doc_test_command` is configured |
| `ratchet.py brief [CPn] --role implementer\|fixer\|test-writer\|reviewer\|scout\|planner` | a sub-agent's opening text: the plan's objective (word for word as `plan-summary` shows it, or a plain no-objective note), then that role's own rules; the orchestrator pastes this at the top of every spawn prompt. For the planner, it also ends with a "What earlier work says" section: recent learnings, findings earlier runs saved for later and nobody accepted yet, and limits the user already accepted, most relevant first then newest, each part capped with a count and a pointer to the full file (`.ratchet/learnings.md`, or a run's `state.json` under `.ratchet/runs/`). It also lists the related recorded specs for the parts being changed (OpenSpec, including Ratchet's own running records; finished Spec Kit features; done BMAD stories), each with its title, a one-line purpose and its path; no earlier work at all gets one plain line instead |
| `ratchet.py view` | the viewer link (live URL if a server runs, else a `file://` link) |
| `ratchet.py view --serve [--open]` | live page on a free port (4477 and up) at `http://127.0.0.1:<port>/` |
| `ratchet.py view --serve --bind 0.0.0.0 [--public-host my-box.local]` | share `http://<host>:<port>/?t=<token>` (requests without the token get 403) |
| `ratchet.py view --stop` | stop the viewer server |
| `ratchet.py pause --reason "..."`, `resume [--claim]`, `cancel` | pause, resume, cancel (resume-claim and cancel are asked about first) |
| `ratchet.py wait --for "..." [--minutes N]` | the run's own chat waits on background reviewers or sub-agents: until then (default 30 minutes, 1 to 120) it may end its turn without a blocked stop |
| `ratchet.py set-review end\|each\|none` | how often you check in |
| `ratchet.py reviewer-status [--reset <id>]` | the host, who is available, and retrying one early (`--reset` clears that provider's shared bench for every lane) |
| `ratchet.py providers [--discover] [--check [id]] [--add <id> --preset <p> --model <m> [--prefer-for <lens>[,<lens>]] [--variant <level|none>]] [--remove <id>]` | agent tools that can review: find, test, add or remove them. `--check` also accepts a reviewer name such as `codex-correctness`; an unknown name is refused with the list of known provider ids. A per-use or unknown-cost model (`providers --discover --json`'s `paid_options`) needs `ask paid-provider` answered first; `--add` for one refuses, with no request sent, until it is |
| `ratchet.py ask paid-provider <tool> <model>` | ask the user whether to spend money on a per-use or unknown-cost model before it is ever used; only their own exact reply, `pay per use: <model>`, counts (never the usual yes words), and only then does `providers --add` send the one test request and add it |
| `ratchet.py models` | which model each role's sub-agent uses on this host |
| `ratchet.py spec detect`, `spec import <openspec\|speckit\|bmad\|auto> <id>`, `spec status` | build from an OpenSpec change, Spec Kit feature or BMAD story; which scenarios are proven so far; `spec keep on\|off` records the user's answer about a running spec |
| `ratchet.py deferred` | review findings noted for later instead of blocking (minor ones, and any blocker/major/unknown-severity one with no `user_impact`), plus, in their own section first, any findings you accepted yourself, with your reason and who accepted them, and a 'fixed later in the run' section for ones `mark-fixed` closed |
| `ratchet.py mark-fixed CPn <ref>="<how>"` | record that a finding left for later was really fixed: refused unless its file changed since the round that raised it AND a later round was recorded on that changed code |
| `ratchet.py receipt [--json]` | one proof receipt to paste into the pull request at the final review: each step's title, commit and elapsed time, the run's own elapsed time (no cost or tokens), who reviewed each step (host-company fallbacks marked, with why when recorded), findings accepted, left for later or fixed later in the run, and every objective done-means line next to the test case(s) that prove it (or "no test linked") |
| `ratchet.py ask accept-finding CPn F1,F2`, `accept-finding CPn` | ask the user to accept named open (blocking) findings of a blocked round, with their own reason (`accept: <reason>`); record it once they reply |
| `ratchet.py ask raise-limit CPn`, `raise-limit CPn` | ask the user to raise a blocked step's own review-round limit by one round, with their own reason (`raise: <reason>`); offered at most once per step, ever, whatever `config.json`'s `max_adversarial_rounds` is later edited to |
| `ratchet.py triage CPn <ref>=fix:"<reason>"` / `<ref>=rebut:"<reason>"` | whenever a round blocks: decide fix or rebut for every blocking finding, even a single one, before the next round opens; never accept/defer/waive |
| `ratchet.py check-in CPn [--decision simplify\|change\|accept\|stop] [--why "<reasoning>"]` | when reviews drag, see (or decide) the check-in against the plan's objective; `accept` is always refused here |
| `ratchet.py merged` | between steps, after merging main into the lane with git (`git merge --no-ff main`, finished and committed): re-runs the earlier steps' checks on the merged code (the regression command when set, else each finished step's own tests), makes the merge the starting point of the next step so its reviewers and commit see only its own changes (a file that differs from git's own clean merge of the two parents, such as a conflict resolved by hand, was reviewed by no one, so it stays in the next step's review and commit and the output names it; a file both sides edited in different places that git merged on its own is not one of them, and a merge made with `-X` or `-s` options may show files as resolved by hand, which is safe; a merge with such files is accepted only while a step is starting to build, and while a step review or the final review waits it is refused with the commit to undo it to, so the agent undoes it, gets the review approved and merges again once the next step has started, or at the final review asks for a step of its own through feedback; a merge git did on its own is accepted in every phase; needs git 2.38 or newer), and accepts standards, design-file and config-rule changes that are exactly main's version (it names each). It asks nothing and approves nothing: anything else that changed (the plan, test plans, the spec, a hand-resolved rule) still needs the user's yes. Refused in the middle of a step (finish it first), when no finished merge is at HEAD, when tracked files have uncommitted edits, when files git does not track yet exist outside `.ratchet/` (commit them in a reviewed step, delete them, or add them to `.git/info/exclude`; `.gitignore` does not work, and interpreter litter like `__pycache__` is ignored), or when the lane added, changed or removed files since the last step finished (those never went through the gates; when `commit_per_checkpoint` is false `complete` records the reviewed tree and `merged` compares with it, so a hand commit of exactly the reviewed work is accepted, and a step that finished without a recorded tree gets a plain refusal instead of an unchecked pass). A path that differs from the step's starting tree counts as a lane commit only if some commit since the step's starting commit touched it (stray untracked files that were never committed are ignored once deleted); a lane whose history no longer includes that starting commit, or a run that did not record it, is refused plainly. Known limit: reviewed work that was never committed and then deleted from disk is not a lane commit, so only the earlier steps' re-run checks guard it). If an earlier step's checks fail on the merged code the run stops and says which; to recover, undo the merge with git (`git reset --keep <the merge's first parent, short sha>`, which refuses instead of discarding edits to the run's files), fix what broke, and once the user lets the run go on, merge main again and run `merged` (a fix committed on top of the merge is not accepted). Run it in the background when a regression suite is set. The guard steers the merge to happen between steps: in the middle of a step it refuses `git merge` and `git pull` with a plain message to finish the step (`complete`) first; between steps it allows them, and while a merge is in progress it lets plain `git add`, `git checkout --theirs/--ours` and `git commit` touch Ratchet's shared files (`.ratchet/config.json`, `standards.md`, `learnings.md`, `.gitignore`) so a conflict there can be resolved. Merge with `git merge --no-ff main`, and `git fetch` then `git merge --no-ff <branch>` instead of `git pull`, so the merge leaves a merge commit. A plain merge or pull that only fast-forwards the lane leaves none, so it cannot be told from the step's own work: Ratchet notices it from HEAD's reflog, and `next` and `tests-written` then say to undo it with `git reset --keep <the old short sha>` and redo it with `--no-ff`. After a merge lands (one in which main only removed files counts too), `next` says to run `merged` first and `tests-written` refuses until it has. A second merge in a step after a hand-resolved one was accepted is refused by the guard (finish the step first); if one lands anyway, `next`, `tests-written` and `merged` name `git reset --keep <its first parent>`. |
| `ratchet.py occurrences <kind>` | the real code where reviewers found a kind of mistake |
| `ratchet.py repeats` | kinds of finding seen twice or more that could become an automatic check |
| `ratchet.py guardrail --kind <k> --title <t> --why <w> --check-command "<c> {target}" --target <dir> --bad-example <fixture> --test-command <c> --test-file <f>` | after you pick the "automatic check" option: add the step that builds it |
| `ratchet.py lanes --scan <dir> [--json]` | each lane's phase, progress and whether it waits on a director |
| `ratchet.py lanes --serve --scan <dir> [--bind 0.0.0.0]` | one dashboard for every lane |
| `ratchet.py config --set key=value` | settings in `.ratchet/config.json` (reviewer roster, retry caps, `regression_command`, `ui_globs`, app URL) |
| `ratchet.py ci-export [--stdout]` | write (or update) `.github/workflows/ratchet.yml`, the GitHub Actions workflow that keeps the run's mechanical checks running on every push and pull request; refuses to replace a hand-written file at that path; `--stdout` prints the workflow instead of writing it |

The viewer server only serves the viewer, its live data and the prototype, never the rest of `.ratchet/`. Its URL is saved in `.ratchet/viewer.json`.

Director-signed approvals: the director's session runs in the director role (see the orchestrator's SKILL.md), creates a key once with `ratchet.py director-key`, then enrolls each lane with `ratchet.py --root <lane> director-enroll`. In an enrolled lane chat messages stop counting as approvals; approvals need `ratchet.py --root <lane> director-sign <action>` and `--director-token <t>`. Builder sessions cannot read the key or sign. The director's per-user folder (`RATCHET_HOME`) and key file must be the same ones the lane's hooks use: Ratchet refuses to run when they differ. `RATCHET_ROLE=director` alone is not enough either: the director commands run only where Ratchet's guard hook recorded a director session, so run the director's agent CLI with Ratchet's hooks, in its own folder (not a lane's), and reach lanes with `--root <lane>`. A builder that sets the variable, even inside a script, is refused. A missing record is refused too, whenever Ratchet's hooks are installed or have run there (restart the director's agent CLI so its hooks record the role). Only when no Ratchet hooks are in use at all is the variable taken at its word, and the command says so, because nothing else is enforced then either. The record of the director's session is written by Ratchet's hook command, so a script deliberately written to cheat, running as your own user, can still forge it by calling that command directly with made-up input; this check stops easy shortcuts, not a deliberate forgery.

The person's override: if you ever need to approve or accept something yourself, outside the chat, run the command in your own terminal with `RATCHET_HUMAN_OVERRIDE=1` set. Ratchet shows a short code in that terminal and goes ahead only after you type it back. An agent's shell has no terminal, so setting the variable there approves nothing. Each use is written to `.ratchet/human_log.jsonl`; if Ratchet can't write that line, it refuses the override and changes nothing. Like the other protections, this stops easy shortcuts, not a script deliberately written to cheat: one running as your own user that starts its own pseudo-terminal, or forges Ratchet's records by calling its hook command directly, can still get past it.

### Running the tests

For the full regression run, use one command. It runs every `tests/test_*.py` suite at the same time and finishes in a few minutes instead of running them one after another:

```
python3 tests/run_all.py
```

It runs half as many suites at a time as the machine has CPU cores, at least 2, because each suite starts processes of its own and one suite per core was slower (`--workers N` changes that), and stops a suite that runs longer than 15 minutes (`--timeout SECONDS`). On a busy machine that limit stretches: it is `--timeout` times the machine's one-minute load average divided by its CPU count, checked again while a suite runs (a load spike extends the limit, and a load drop never takes back time already granted: the highest limit reached stays in force), never less than `--timeout`, and never more than `--max-stretch` times it (default 4; `--max-stretch 1` turns the stretch off), so a suite that is only slow because the machine is busy is not failed for it. When a suite ran past its `--timeout` or was stopped at a stretched limit, the summary says so, with the factor, both limits (the highest reached) and the load. Each suite gets its own empty temp folder, and a suite that leaves anything in it is reported as failed even when its own checks passed. When a suite ends, run_all.py also stops every process it started that is still running, including one it detached into its own session, by a tag (`RATCHET_RUN_ALL_TAG`) each suite's processes inherit; on a system without `/proc` it can only stop the suite's process group, and its summary says so. The slowest suites start first: run_all.py remembers how long each suite took last time in a small file in your user cache folder (`~/.cache/ratchet-loop/`, never in the repository) and, for a suite it has not timed yet, guesses from the file's size. At the end it shows the last lines of output of every failed suite, a table of every suite and its time, slowest first, and a `N passed, M failed` line counting suites. It exits non-zero when any suite failed or none were found.

Each test file is a standalone script; none needs a framework, so you can also run one suite on its own. Every suite makes its temp folders through `tests/_tmp.py`, which removes them, and stops any background server the suite started, when the suite ends, whether it passes, fails a check or crashes. The one exception is `tests/test_cleanup.py`, which tests that helper and so makes and removes its own throwaway folders without it. `tests/test_runner.py` tests run_all.py itself against tiny made-up suites and never runs the real ones.

```
python3 tests/test_accept_finding.py
python3 tests/test_brief.py
python3 tests/test_brief_guard.py
python3 tests/test_checkin.py
python3 tests/test_ci_export.py
python3 tests/test_ci_export_docs.py
python3 tests/test_ci_export_shapes.py
python3 tests/test_ci_export_write.py
python3 tests/test_cleanup.py
python3 tests/test_complete_foreground.py
python3 tests/test_converge.py
python3 tests/test_dedup_unknown_model.py
python3 tests/test_deferred_fixed.py
python3 tests/test_digest.py
python3 tests/test_digest_docstring.py
python3 tests/test_digest_specs.py
python3 tests/test_discover.py
python3 tests/test_discover_omp.py
python3 tests/test_discover_paid.py
python3 tests/test_docs.py
python3 tests/test_docs_accuracy.py
python3 tests/test_flaky.py
python3 tests/test_feedback_fixes.py
python3 tests/test_fix_round.py
python3 tests/test_fix_round_diff.py
python3 tests/test_fix_round_diff_fixes.py
python3 tests/test_fix_round_fixes.py
python3 tests/test_gates.py
python3 tests/test_guard_python_reads.py
python3 tests/test_guard_reads.py
python3 tests/test_guard_reserved_names.py
python3 tests/test_guard_writes.py
python3 tests/test_guardrails.py
python3 tests/test_hooks.py
python3 tests/test_hooks_status.py
python3 tests/test_hooks_waiver.py
python3 tests/test_install.py
python3 tests/test_lane_added_tests.py
python3 tests/test_limits.py
python3 tests/test_load_timeouts.py
python3 tests/test_load_timeouts_fixes.py
python3 tests/test_merge_guard.py
python3 tests/test_merge_main.py
python3 tests/test_objective.py
python3 tests/test_opencode_big.py
python3 tests/test_plan_change.py
python3 tests/test_providers.py
python3 tests/test_question_waiting.py
python3 tests/test_quick_path.py
python3 tests/test_quick_path_docs.py
python3 tests/test_quick_path_docs_step.py
python3 tests/test_quick_path_records.py
python3 tests/test_quick_path_small.py
python3 tests/test_quota_check.py
python3 tests/test_quota_refill.py
python3 tests/test_quota_reset.py
python3 tests/test_quota_share.py
python3 tests/test_quota_shared.py
python3 tests/test_quota_spread.py
python3 tests/test_receipt.py
python3 tests/test_receipt_proof.py
python3 tests/test_related_tests.py
python3 tests/test_related_tests_fixes.py
python3 tests/test_results.py
python3 tests/test_review_objective.py
python3 tests/test_run_all_dedupe.py
python3 tests/test_runner.py
python3 tests/test_runner_load.py
python3 tests/test_runner_load_fixes.py
python3 tests/test_signing.py
python3 tests/test_specs.py
python3 tests/test_split_check.py
python3 tests/test_stale_lock.py
python3 tests/test_status_plain.py
python3 tests/test_stop_on_change.py
python3 tests/test_stop_on_change_fixes.py
python3 tests/test_trust_cp1.py
python3 tests/test_trust_cp3.py
python3 tests/test_trust_cp4.py
python3 tests/test_trust_cp5_a.py
python3 tests/test_trust_cp5_b.py
python3 tests/test_trust_cp5_c.py
python3 tests/test_trust_cp5_d.py
python3 tests/test_trust_cp5_e.py
python3 tests/test_trust_cp5_regression.py
python3 tests/test_viewer.py
python3 tests/test_waiting.py
```

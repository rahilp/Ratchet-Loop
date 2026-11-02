# Using Ratchet Loop

[← Back to the README](../README.md) · [How it works](how-it-works.md) · [Reference](reference.md)

Everything here is done by talking to your agent. This page walks through a run from start to finish, what Ratchet creates in your project, and how approvals work.

## What happens next

First the agent does some quick checks on its own. Are the hooks working? Which other AI tools are installed that could review the work? Has your team already written a spec for this? If anything needs your OK, like installing the hooks or starting a new git branch, it asks. If it finds a problem that would get in the way (the app won't start, there's no way to run tests, the screenshot tool is missing, or you have unsaved changes), it tells you in one message and offers to fix each one.

Then it shows you the plan, right in the chat. It opens with the objective: who the result is for, what they want, what done means to them, and what's out of scope (for example users: "managers who approve leave requests"; goal: "clear a team's leave requests without leaving the app"; done means: "a manager can approve or reject a request with a reason"; out of scope: "approving another team's requests"). Every step, every reviewer and every finding points back to it, so the build stays aimed at what you actually asked for. Every done-means line is linked to at least one planned test case that proves it, and the plan summary shows those cases under each line; the engine refuses a plan that leaves a line unlinked before you ever see it, so there is no line that nothing will check. Below that you'll see each step, what "done" means for it, whether its look will be checked, how risky it is and who will review it. Read it. Say "yes" to start, or tell it what to change. If the feature has new screens, you also get a clickable prototype to look at first, so you can see what it'll look like before anything is built. Once you say yes, the plan, the design file and the prototype are locked. The agent can't change them later without asking you.

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

### Accepting an unproven done-means line yourself

At the final review, a done-means line that is unproven (a linked test failed or has no result, no test is linked, or an open finding names it) holds the finish. If you decide that line can stay unproven for now (the work ships next sprint, the limit is tracked elsewhere), you can say so yourself, once, with your own reason, and the finish is released for that line only. The agent can't do this on its own: it runs `ask accept-line <n>` for the line numbers the table shows (only an unproven line can be asked about; a line that is already proven, already accepted, or not a real line is refused by number), and shows you the line with the exact reply that accepts it:

> accept: <your reason>

Only your first reply after that question, in those exact words plus your own reason, accepts the lines it named; a plain yes, a reply in another chat, or anything typed before the question accepts nothing, and a first reply in another form closes the question (the agent has to ask again). Each line you accept is written down with your reason, in `.ratchet/learnings.md` and in the run's own record, and from then on the final-review table and the proof receipt show that line as **accepted**, with what held it (the failed test, or the open finding) and your reason, apart from the lines the tests proved; it is never relabelled as proven. Your acceptance covers only what held the line at that moment: if the same line later goes unproven for a new reason (a test linked to it fails after you asked for changes, or a reviewer files a new finding against it), the table shows it as unproven again, marked `held again` with your earlier acceptance and the new cause, and the finish is held until it is fixed or you accept it again. Accepting one line never clears another: every other unproven line still holds the finish until it is proven or you accept it too, and a yes to the final review is still needed afterwards. A run whose plan was approved before this rule has nothing to accept (its unproven lines never held the finish), so the agent just says so.

In a director-enrolled lane, a chat reply never accepts a line: the director signs it instead (`director-sign accept-line`, after the agent's `ask accept-line`), and the agent runs `accept-line --director-token <t> --reason "<the director's reason>"`, the same as every other approval in that lane.

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


%% latexa11y-assignment.cls
%% Accessible homework / problem-set class.
%%
%%   \documentclass[sol]{latexa11y-assignment}
%%
%% Class options replace the corpus convention of keeping two near-identical
%% driver files (prob9.tex and sol9.tex differing only in whether \sol expands
%% to nothing). One source, two builds.
%%
%%   prob       student version, solutions hidden          (default)
%%   sol        full solutions
%%   ans        answers only, no worked solutions
%%   guidance   also show staff-only guidance blocks
%%   noreserve  do not reserve blank space where a solution is hidden
%%   draft      downgrade unfilled-alt errors to warnings (migration only)

\NeedsTeXFormat{LaTeX2e}[2020-10-01]
\ProvidesClass{latexa11y-assignment}[2026-08-08 v0.1.0 Accessible assignment]

\newif\ifaccess@sol      \access@solfalse
\newif\ifaccess@ans      \access@ansfalse
\newif\ifaccess@guidance \access@guidancefalse
\newif\ifaccess@reserve  \access@reservetrue
\newif\ifaccess@draft    \access@draftfalse

\DeclareOption{prob}{\access@solfalse\access@ansfalse}
\DeclareOption{sol}{\access@soltrue\access@anstrue}
\DeclareOption{ans}{\access@anstrue}
\DeclareOption{guidance}{\access@guidancetrue}
\DeclareOption{noreserve}{\access@reservefalse}
\DeclareOption{draft}{\access@drafttrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax

\LoadClass[11pt]{article}

\RequirePackage[margin=1in]{geometry}
\RequirePackage{amsmath}
\RequirePackage{graphicx}
\RequirePackage{enumitem}
\RequirePackage{xcolor}
%% hyperref before bookmark; both are needed because tagging never writes an
%% outline -- /StructTreeRoot and /Outlines are separate object graphs.
\RequirePackage{hyperref}
\RequirePackage{bookmark}
\hypersetup{hidelinks}

\RequirePackage{latexa11y-doc}

\ifaccess@sol\accessshowsolutions\fi
\ifaccess@ans\accessshowanswers\fi
\ifaccess@guidance\accessshowguidance\fi
\ifaccess@reserve\else\accessnoreserve\fi
\ifaccess@draft\accesssetup{strict=false}\fi

\endinput

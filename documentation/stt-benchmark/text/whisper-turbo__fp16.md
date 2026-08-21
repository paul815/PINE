# whisper-turbo__fp16

| | |
|---|---|
| модель | `F:/Downloads/AI Stuff/stt-bench/models/turbo-ct2` |
| время расшифровки | 19.17 с |
| пик VRAM | 3336 МБ |
| сегментов | 162 |
| пословных таймингов | 3089 |

---

**00:00**  represent per page. So like, each string represents whatever is on an entire page. And then the last

**00:09**  page that the user looked at, this would just be some integer, and this would be an index into this

**00:14**  list of strings. So I would have to remember that this, you know, there might be an off by one error

**00:22**  here. Because in Python, we start at zero. But like, naturally, when we're reading a book, we might

**00:28**  start at one. Yep. Now for this library, for this collection of books, okay, well, so for this

**00:35**  active book, I, you could either use a Boolean, where you set one book to active and the rest to

**00:44**  inactive. But I think that that would be kind of excessive, because every single time we toggle a

**00:50**  book as active, we have to toggle everything else as inactive, right? You can't have two active books.

**00:55**  Yeah, yeah. Okay. Yeah, that makes sense. Okay. So then I think in that case, instead,

**01:03**  what I would rather do is have some sort of a string or some sort of like an ID matching system,

**01:12**  where we have like one variable that's set to the active book. And that might be the title or the ID

**01:21**  of a certain book in the library. So maybe, maybe we can just add ID here. And maybe I can make that

**01:30**  also a string or an integer or something. Yep. And I'm going to add a question mark to kind of just say

**01:36**  like, maybe we could implement that, but I'm not sure if we'd want to right now.

**01:40**  Mm-hmm. So this would correspond to ID. So this would just be some variable. And then this collection

**01:57**  of books. So I guess like, when we display this page in an active book, I actually, so I think that

**02:12**  this might have an advantage being like some sort of a lookup table because we have a certain sense

**02:20**  of an ID, right? And I think what could make sense here is having the ID correspond to the book object

**02:27**  that we define up here. So this could be a book object, right? And so we don't really need this ID.

**02:35**  I think that if all the titles are unique, then we don't need this ID. We can just use the titles

**02:42**  as the IDs. But in some cases, in the real world, not all titles are unique. And so that's when an ID

**02:50**  might come in handy. And so I'll leave that, like, what are our assumptions here? Can we assume that the

**02:59**  titles are unique or should I be using this ID structure?

**03:01**  I think I like the ID structure because, yeah, we're not necessarily going to know that

**03:06**  everything's unique. So I think this is a little bit more robust.

**03:10**  Okay. Awesome. So I think this representation looks good to me. So I'm just going to go through

**03:17**  the requirements again, just to make sure that I've covered all my bases. So here I want all books.

**03:24**  Okay. So I want a library of books that I can add to or remove from. And so here I have a library.

**03:29**  And anytime I want to add to this library, I can just add an ID. I can add the book. If I want to

**03:35**  remove from the library, then I can just simply delete the key item pair or the key, whatever pair,

**03:46**  key value pair. I can set a book from their library as active. And so here I have this active book.

**03:53**  And we could always say like, if there is no active book, then we set this to none or something like

**03:59**  that. The reading application remembers where a user left off. So we have that here, right? The last

**04:06**  page that the user looked at and not just in the active book. And then displaying one page of text

**04:13**  at a time in the active book. So we have the active book. We can get the book object from that. And then

**04:20**  we can go to the page that the user last looked at by indexing this value into this list. So

**04:29**  I think we've covered all the bases here. Cool. Let's do a couple of things.

**04:34**  I guess, I think as a starting point, and this doesn't have to get too crazy, but I would love

**04:40**  to just see some, you know, Python pseudocode or even some Python kind of code just to like maybe

**04:44**  flesh out one of these classes. I just want to, you know, look at that a little bit and how you would

**04:49**  actually start writing this code. Yeah, sure. So, so for example, here, I let's start with the book.

**04:59**  And when we define a class, let's initialize this. So when we initialize a book, we definitely

**05:08**  should pass in the title, and then the content, right? I'm assuming that when we add a book,

**05:14**  these things have to be given to us. And then I'm assuming that when we add a book, we don't have a

**05:23**  last page that the user has looked at. Is that an okay assumption? Yeah, I like that assumption.

**05:27**  Okay. So we can set the title of this book just equal to whatever the title is,

**05:34**  the content of this book. And I'm going to assume that these are given to us in the formatting that

**05:41**  we want. So the title is a string and the content is a list of strings that correspond to the pages.

**05:46**  Um, and I'm going to create a variable last page, and I'm going to set that equal to just zero. I could

**05:54**  also probably set it equal to negative one or something, but I think zero makes sense because

**05:58**  that would be the beginning, right? Yep. And, um, and then we need to come up with some sort of an ID.

**06:06**  So typically, maybe we would have some sort of function that takes in the title and the content or

**06:13**  maybe the author or something and like returns a unique ID. Um, but I think maybe something that we can do

**06:22**  here just very simply is, uh, have some sort of counter in the library. And then every single

**06:28**  time we like add, add a new book, we can just, uh, associate that counter with that book. And so

**06:34**  then we know that they're all unique and, um, and they all have their own IDs. Would that make sense?

**06:42**  Yeah, that, that makes sense.

**06:44**  Okay. So then I would have to pass in the ID. Um, yeah. And so what I'm actually going to do is

**06:53**  self dot ID equals ID. Uh, and then, uh, yeah. And so then, uh, for example, we might want like a display

**07:03**  page, right? In this book. And so for that, then we just want to return, um, whatever's at the last

**07:14**  page of the content. Mm-hmm. And I guess let's build on this a little bit. Uh, another useful function,

**07:21**  you know, obviously you want to start at the last page, but let's say we're starting to turn the

**07:26**  page. What might that look like? Right. And so if we're turning the page, then all we want to do

**07:36**  is increment the last page. Right. And so we might do self dot last page and we just increment that by

**07:44**  one. Um, and then we could call display page after that. Uh, if we assume that, like we could, if,

**07:56**  if we assume that these two go together, so if we assume that when we turn the page, we want to

**08:00**  display that page, then I would just return display page. Yep. Like that. Um, and then actually,

**08:12**  I'm just going to go ahead and code up this library as well. So this library, we have this collection

**08:21**  of books and then also the active book. So rather than, uh, initializing a library with books, I think

**08:30**  let's just add them, um, to this. So initially this collection might just be an empty dictionary

**08:39**  and then the active book might be done. Right. So then, um, when we want to add stuff, so add to

**08:51**  collection, uh, book what I'm going to do. So there's either the user could like, or our API could like

**09:10**  return a book with the, uh, with the title and the content, et cetera. But since we're, we said that

**09:17**  we would pass the ID through like the library because we might have like a counter, like some

**09:23**  sort of ID counter. I mean, this is not the best way to do it, but I think this is like a fine hack

**09:28**  for right now, um, just to make all the IDs unique. What we can do here is, um, we can also, when we want

**09:39**  to add a book, just pass in the title and the content. And so then our new book is going to be a book

**09:48**  as we've defined above with the ID and, um, the title and the count or the content. And then after

**09:59**  we create this new book, we want to increment the ID counter. Uh, and then we also want to add this new

**10:08**  book into the collection, right? So I'm going to add self dot collection. Um, and I'm going to

**10:19**  make the ID, whatever the ID of the book is. So actually I'm just going to call new book dot ID.

**10:25**  Cause I think that's a bit cleaner and this is going to be the new book. And then we increment

**10:30**  the counter by one. So this is us adding to the collection. Um, so of course we want to remove

**10:38**  from the collection. And so we want, we said that we wanted to remove based on the ID. So all we have

**10:45**  to do is I think there's this delete in Python. I'm not sure if there is. Do you know Keith?

**10:58**  Okay. Is there a remove? Let's see. I think that there's, I mean, it should autocomplete here

**11:05**  in, um, the coder pad. Oh, isn't it Dell? Isn't there a Dell? Um, I mean, I think the biggest thing

**11:12**  is, is it going to be happening in place or is it going to be happening in creating a copy? Um,

**11:18**  I, I understand. Yeah. So we want it in place from the collection. I understand what you're trying

**11:23**  to do. So the exact details is, is not super, super important to me. Okay. Yeah. So anyways,

**11:30**  this is removing from the collection. Um, and when we set active book again, we want to do this based

**11:38**  on the ID. So I'm going to make self dot active book equal to whatever that ID is. Uh, and let's see.

**11:53**  So the user has a library of books they can add or remove from. We did that setting a book as active.

**11:59**  Okay. And then remembering where user left off. So we've actually taken care of that already

**12:04**  implicitly in our, um, book, uh, class. And then the reading application only displays a page of text

**12:11**  at a time. All right. So then here we can say define display page. And actually we don't even need

**12:20**  the, the ID of the book because we already have the active book ID. Right. And so what I'm going to

**12:26**  do is I'm going to go into my collection, um, and I'm going to get the active book like this. So

**12:39**  Yep. Blah, blah, blah. And I'm going to click, or I'm going to do dot display page. Okay.

**12:48**  And then of course we also have this turn page that I could, um, just put up as well. Yep.

**13:00**  You get the idea, but so we want to oops, turn that page. Um, I do think that some like drawbacks

**13:14**  here are that, uh, okay. So one thing that we could have done is instead of this active book,

**13:22**  instead of saving the ID, we could have saved the book itself. And I think from a pipe pythonic

**13:27**  standpoint, that might make sense. But then in the future, if we're using some sort of database or

**13:31**  something, an ID would make more sense because that's easier to store in like a table. Um,

**13:38**  and if, uh, and okay, also for this display page, you might see that we've called this display page

**13:44**  and turn page up here in the book. And the reason why I did that rather than, um, rather than just

**13:53**  call like self dot book dot content, last page, like book dot last page or something

**14:00**  was because I think that like, for example, this should be independent of how we implement the book.

**14:07**  Right. So we want to keep those as kind of block, like separate blocks as possible.

**14:14**  And so here, what this allows me to do is when I go back to my book, like if I decided to scrap

**14:20**  this implementation, implement something else, I know that I would just have to implement the

**14:24**  methods display page and turn page for that to work. Um, and like these functions. Cool. This,

**14:32**  this looks good to me. I like the start. I think that this, uh, definitely lays out the foundation of

**14:37**  what I was looking for with the requirements of this application. So nice work with this couple

**14:43**  quick followup questions before we kind of branch into more of an algorithms, uh, type question as,

**14:50**  as the followup here. Um, one question is, you know, let's say you have an older reader and their

**14:57**  vision isn't as great. So they need to make this, the size of the font bigger. So kind of keeping that

**15:03**  in mind that you might have this variable font size, how might you, I guess, refactor or change or modify

**15:09**  your current structure and you don't have to actually write code here. Let's just discuss this

**15:13**  to account for, um, that increase in size. Yeah. Yeah. So I, so when we increase something in size,

**15:26**  like I'm just thinking about intuitively on our like phone screens or something, um, that type,

**15:30**  that tends to shift the content, right? So like some page that might hold a hundred words or a hundred

**15:36**  characters at, you know, um, a small font size might only hold 50 if when we double that font size.

**15:44**  So, uh, for the book, instead of having this list, what I might do is I might just save the entire book

**15:53**  as a string and, um, and based, so I would, let me just edit this in here. Cause I think it's easier to

**16:01**  visualize what I'm saying, but so I would, my, I might have like a font size equal to, um,

**16:12**  I guess we could just use like the traditional, like 12 point font or like whatever. Uh, but

**16:18**  when we have this font size, we could come up with some sort of, um, characters per page calculation.

**16:26**  Right. And so that would be based off the font size. So calculate, so this is going to be pseudo.

**16:32**  Yeah. Now this calculate doesn't actually exist, but calculate, uh, based on that font size.

**16:39**  Um, and I'm commenting it out so it doesn't have any errors, but then, uh, when we do calculate

**16:48**  that, then what we can do in order to get our, um, in order to get this display page thing would be

**16:55**  instead of indexing into this content, which is now just a long string of characters.

**17:04**  What we can do here is we can return, um, we would have to do some sort of calculation,

**17:10**  right? So we would need to, we have this characters per page and we can multiply that by whatever the

**17:19**  last page is. And, um, um, so this would give us like kind of the starting index of this long string

**17:28**  of characters to start, uh, the content of that page. Start index would be this. And then what we would

**17:38**  want to do is, um, what we would want to do is then return the self doc content from that start index

**17:50**  until the start index plus the number of characters on that page. So that would be our end index. So

**17:58**  actually let me just write that out to make things a little bit more clear. So now essentially what

**18:09**  I've done is we have this variable font size, which we can calculate characters per page depending on

**18:15**  that font size. So that might be more characters or less characters depending on how big the font size

**18:20**  is. And then our content instead of a list of strings is now just a really long string of characters.

**18:26**  And I guess some assumptions that I'm making here are that, uh, this would just be approximate.

**18:32**  Um, so for example, if we had a backslash n, which is like a line break, right? Um, that might just

**18:39**  end up counting as a character or two characters, but the assumption is that, um, that would be

**18:45**  approximate per page, or maybe that we're using source, some sort of like mono spaced font or something

**18:50**  like that. Um, but essentially now that we have the characters per page, and then just a long stream

**18:57**  of characters that represents the content we can index into that content depending on like based on the

**19:04**  last page that we know. And so what's really cool here is that when we do turn the

**19:08**  page, uh, we can just end up calling it. So this is kind of that granularity that I was talking about.

**19:14**  Yeah, that, that makes a lot of sense to me. And definitely you see the advantage of having

**19:19**  that function that you can change by having to implement something different.

**19:23**  Right.

**19:23**  Uh, one last quick, I guess, follow up and then we'll, we'll move on to the algorithms portion

**19:27**  of this interview. Um, I'm just trying to think, you know, at a high level, let's say that we wanted,

**19:33**  we had multiple users in this system and they all shared books. So like, you know,

**19:39**  they might all share the Harry Potter books or something like that. Like books are common

**19:43**  among all of them. How might you modify this? Yeah. Uh, given that, you know, some of these

**19:49**  qualities of a book might be shared between users because they're all trying to access that same book.

**19:54**  Does that make sense? Yeah. So, um, yeah, yeah, yeah, for sure. So I,

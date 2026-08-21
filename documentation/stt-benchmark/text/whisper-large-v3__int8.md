# whisper-large-v3__int8

| | |
|---|---|
| модель | `F:/Downloads/AI Stuff/pine-Mac_Improvements/PINE-RC-v1.0.0/models/whisperx-large-v3` |
| время расшифровки | 126.15 с |
| пик VRAM | 4814 МБ |
| сегментов | 255 |
| пословных таймингов | 3108 |

---

**00:00**  represent per page so like each string represents whatever is on an entire page

**00:07**  and then the last page that the user looked at this would just be some

**00:11**  integer and this would be an index into this list of strings so I would have to

**00:16**  remember that this you know there might be an off by one error here because in

**00:24**  Python we start at zero but like naturally when we're reading a book we

**00:28**  might start at one yep now for this library for this collection of books

**00:34**  okay well so for this active book I you could either use a boolean where you set

**00:41**  one book to active in the rest of two inactive but I think that that would be

**00:46**  kind of excessive because every single time we toggle a book as active we have

**00:51**  to toggle everything else as inactive right you can't have two active books

**00:55**  yeah yeah all right okay

**00:58**  you can't have two active books yeah yeah okay

**00:58**  yeah that makes sense okay so then I think in that case instead what I would

**01:03**  rather do is have some sort of a string or some sort of like an ID matching

**01:11**  system where we have like one variable that's set to the active book and that

**01:19**  might be the title or the ID of a certain book in the library so maybe

**01:25**  maybe we can just add ID here

**01:27**  and maybe I can make that also a string or an integer or something yep and I'm

**01:34**  gonna add a question mark to kind of just say like maybe we could implement

**01:38**  that but I'm not sure if we'd want to right now so this would correspond to ID

**01:49**  so this would just be some variable and then this collection of books is going to

**01:58**  So, I guess like when we display this page in an active book, I actually, so I think that this might have an advantage being like some sort of a lookup table because we have a certain sense of an ID, right?

**02:21**  And I think what could make sense here is having the ID correspond to the book object that we define up here.

**02:28**  So, this could be a book object, right?

**02:32**  And so, we don't really need this ID.

**02:35**  I think that if all the titles are unique, then we don't need this ID.

**02:40**  We can just use the titles as the IDs.

**02:44**  But in some cases, in the real world, not all titles are unique.

**02:48**  And so, that's when an ID might come in handy.

**02:53**  And so, I'll leave that, like, what are our assumptions here?

**02:57**  Can we assume?

**02:58**  That the titles are unique or should I be using this ID structure?

**03:02**  I think I like the ID structure because, yeah, we're not necessarily going to know that everything's unique.

**03:06**  So, I think this is a little bit more robust.

**03:10**  Okay.

**03:11**  Awesome.

**03:11**  So, I think this representation looks good to me.

**03:15**  So, I'm just going to go through the requirements again just to make sure that I've covered all my bases.

**03:21**  So, here I want all books.

**03:24**  Okay.

**03:25**  So, I want a library of books that I can add to or remove from.

**03:28**  And so, here I have a library.

**03:29**  And anytime I want to add to this library, I can just add an ID.

**03:33**  I can add the book.

**03:34**  If I want to remove from the library, then I can just simply delete the key item pair or the key whatever pair, key value pair.

**03:48**  I can set a book from their library as active.

**03:51**  And so, here I have this active book.

**03:53**  And we could always say, like, if there is no active book, then we can just remove it.

**03:58**  We set this to none or something like that.

**04:00**  The reading application remembers where a user left off.

**04:04**  So, we have that here, right?

**04:05**  The last page that the user looked at and not just in the active book.

**04:10**  And then displaying one page of text at a time in the active book.

**04:15**  So, we have the active book.

**04:17**  We can get the book object from that.

**04:19**  And then we can go to the page that the user last looked at by indexing this value into this list.

**04:28**  So, I think we've covered all the bases here.

**04:30**  Cool.

**04:32**  Let's do a couple things.

**04:34**  I guess, I think as a starting point, and this doesn't have to get too crazy, but I would love to just see some, you know, Python pseudocode or even some Python kind of code.

**04:44**  Just to, like, maybe flesh out one of these classes.

**04:46**  I just want to, you know, look at that a little bit and how you would actually start writing this code.

**04:52**  Yeah, sure.

**04:53**  So, for example, here, let's start with the book.

**04:59**  And when we define a class, let's initialize this.

**05:05**  So, when we initialize a book, we definitely should pass in the title and then the content, right?

**05:13**  I'm assuming that when we add a book, these things have to be given to us.

**05:17**  And then I'm assuming that when we add a book, we don't have a last page that the user has looked at.

**05:24**  Is that an okay assumption?

**05:25**  Yeah, I like that assumption.

**05:27**  Okay.

**05:28**  So.

**05:29**  We can set the title of this book just equal to whatever the title is, the content of this book.

**05:36**  And I'm going to assume that these are given to us in the formatting that we want.

**05:42**  So, the title is a string and the content is a list of strings that correspond to the pages.

**05:47**  And I'm going to create a variable last page, and I'm going to set that equal to just zero.

**05:54**  I could also probably set it equal to negative one or something, but I think zero makes sense because that would be the beginning.

**05:59**  Right?

**06:00**  Yep.

**06:02**  And then we need to come up with some sort of an ID.

**06:06**  So, typically, maybe we would have some sort of function that takes in the title and the content or maybe the author or something and returns a unique ID.

**06:18**  But I think maybe something that we can do here just very simply is have some sort of counter in the library.

**06:27**  And then every single time we add.

**06:29**  Add a new book, we can just associate that counter with that book.

**06:34**  And so, then we know that they're all unique and they all have their own IDs.

**06:41**  Would that make sense?

**06:42**  Yeah, that makes sense.

**06:44**  Okay.

**06:45**  So, then I would have to pass in the ID.

**06:49**  Yeah.

**06:50**  And so, what I'm actually going to do is self.id equals ID.

**06:56**  And then.

**06:59**  And so, then, for example, we might want like a display page, right, in this book.

**07:06**  And so, for that, then we just want to return whatever is at the last page of the content.

**07:17**  And I guess let's build on this a little bit.

**07:20**  Another useful function, you know, obviously, you want to start at the last page.

**07:24**  But let's say we're starting to turn the page.

**07:26**  What might that look like?

**07:29**  Right.

**07:30**  And so.

**07:30**  So, if we're turning the page, then all we want to do is increment the last page, right?

**07:39**  And so, we might do self.lastPage.

**07:43**  And we just increment that by one.

**07:45**  And then we could call displayPage after that.

**07:51**  If we assume that.

**07:54**  Like, we could.

**07:55**  If we assume that these two go together.

**07:57**  So, if we assume that when we turn the page, we want to display that page.

**08:01**  Then I would just return displayPage.

**08:04**  Yep.

**08:06**  Like that.

**08:10**  And then, actually, I'm just going to go ahead and code up this library as well.

**08:16**  So, this library, we have this collection of books.

**08:22**  And then also the active book.

**08:24**  So, rather than initializing a library with books, I think let's just add them to this.

**08:33**  So, initially, this collection might just be an empty dictionary.

**08:39**  And then the active book might be none, right?

**08:44**  So, then when we want to add stuff.

**08:50**  So, add to collection book.

**09:00**  What I'm going to do.

**09:02**  So, there's either the user could, like, or our API could, like, return a book with the title and the content.

**09:15**  So, if we want to add a book, we can just pass in the title and the content.

**09:16**  But since we're, we said that we would pass the ID through, like, the library.

**09:20**  Because we might have, like, a counter.

**09:23**  Like, some sort of ID counter.

**09:24**  I mean, this is not the best way to do it.

**09:26**  But I think this is, like, a fine hack for right now.

**09:31**  Just to make all the IDs unique.

**09:33**  What we can do here is we can also, when we want to add a book, just pass in the title and the content.

**09:43**  And so, then.

**09:45**  Our new book is going to be a book, as we've defined above, with the ID and the title and the count, or the content.

**09:58**  And then after we create this new book, we want to increment the ID counter.

**10:06**  And then we also want to add this new book into the collection, right?

**10:09**  So, I'm going to add self.collection.

**10:16**  And I'm going to make the ID, whatever the ID of the book is.

**10:22**  So, actually, I'm just going to call newbook.id because I think that's a bit cleaner.

**10:26**  And this is going to be the new book.

**10:29**  And then we increment the counter by one.

**10:31**  So, this is us adding to the collection.

**10:36**  So, of course, we want to remove from the collection.

**10:39**  And so, we want, we said that we wanted to remove based on the ID.

**10:43**  So.

**10:45**  All we have to do is, I think, there's this delete in Python.

**10:53**  I'm not sure if there is.

**10:54**  I don't remember.

**10:55**  Do you know, Keith?

**10:57**  Okay.

**10:59**  Is there a remove?

**11:02**  Let's see.

**11:02**  I think that there is.

**11:03**  I mean, it should autocomplete here in the coder pad.

**11:06**  Oh, isn't it Dell?

**11:08**  Isn't there a Dell?

**11:11**  I mean, I think the biggest thing is, is it going to be happening in place?

**11:14**  I think this is valid.

**11:14**  Or is it?

**11:15**  I think this is going to be happening in creating a copy.

**11:18**  I understand.

**11:19**  Yeah.

**11:20**  So, we want it in place from the collection.

**11:22**  I understand what you're trying to do.

**11:23**  So, the exact details is not super, super important to me.

**11:28**  Okay.

**11:29**  Yeah.

**11:30**  So, anyways, this is removing from the collection.

**11:33**  And when we set active book, again, we want to do this based on the ID.

**11:39**  So, I'm going to make self.active book.

**11:44**  So, I'm going to make self.active book equal to whatever that ID is, and let's see, so the user has a library of books they can add or remove from, we did that, setting a book as active, okay, and then remembering where a user left off, so we've actually taken care of that already implicitly in our book class, and then the reading application only displays a page of text at a time.

**12:12**  All right.

**12:12**  So, then here we can say define display.

**12:17**  Page, and actually, we don't even need the ID of the book because we already have the active book ID, right?

**12:25**  And so, what I'm going to do is I'm going to go into my collection, and I'm going to get the active book like this, so, blah, blah, and I'm going to click, or I'm going to do dot display page.

**12:48**  And then, of course, we also have this turn page that I can use.

**12:51**  I could just code up as well.

**12:58**  Yep.

**13:00**  You get the idea.

**13:09**  I do think that some drawbacks here are that, okay, so one thing that we could have done is, instead of this active book, instead of saving the ID, we could have saved the book itself.

**13:26**  And I think from a Pythonic standpoint, that might make sense.

**13:29**  Okay.

**13:29**  But then in the future, if we're using that as well.

**13:30**  some sort of database or something an id would make more sense because that's easier to store

**13:34**  in like a table um and if uh and okay also for this display page you might see that we've called

**13:44**  this display page and turn page up here in the book and the reason why i did that rather than

**13:51**  um rather than just call like self.book.content last page like book.last page or something

**13:59**  was because i think that like for example this should be independent of how we implement the

**14:07**  book right so we want to keep those as kind of block like separate blocks as possible

**14:13**  and so here what this allows me to do is when i go back to my book like if i decide to scrap

**14:20**  this implementation implement something else i know that i would just have to implement the

**14:24**  methods display page and turn page for that to work um

**14:28**  um

**14:28**  and like these functions cool this this looks good to me i like the start i think that this uh

**14:34**  definitely lays out the foundation of what i was looking for with the requirements of this

**14:40**  application so nice work with this a couple quick follow-up questions before we kind of branch into

**14:46**  more of an algorithms uh type question as as the follow-up here um one question is

**14:53**  you know let's say you have an older reader and their vision isn't as great so they need to make

**14:58**  this the size of the font bigger so kind of keeping that in mind that you might have this

**15:04**  variable font size how about you i guess refactor or change or modify your current structure and you

**15:11**  don't have to actually write code here let's just discuss this to account for um that increase in

**15:18**  size yeah yeah so i so when we increase something in size like i'm just thinking about intuitively

**15:27**  on our like phone screens or something like that i'm just thinking about how to make it bigger and

**15:28**  with the later people like subjectatif and like you know like sm light or like bigfoot or something

**15:29**  um that that tends to shift the content right so like some page that

**15:33**  old 100 words or a hundred characters that last 10 years but like your maybe using come up with

**15:38**  at you know um a small font size might only old 50 if when we double that font size so uh

**15:45**  for the book instead of having m

**15:49**  this list what i might do is i might just save the entire book as a string and um and based so

**15:58**  So I would, let me just edit this in here, because I think it's easier to visualize what

**16:02**  I'm saying.

**16:03**  But so I would, I might have like a font size equal to, I guess we could just use like the

**16:13**  traditional like 12 point font or like whatever.

**16:17**  But when we have this font size, we could come up with some sort of characters per page

**16:25**  calculation, right?

**16:27**  And so that would be based off the font size.

**16:29**  So calculate, so this is going to be pseudocode now.

**16:32**  This calculate doesn't actually exist, but calculate based on that font size and I'm

**16:43**  commenting it out so it doesn't have any errors.

**16:45**  But then when we do calculate that, then what we can do in order to get our, in order to

**16:53**  get this display page thing would be instead of indexing into this content, which is now

**16:57**  just.

**16:59**  A long string of characters.

**17:04**  What we can do here is we can return, um, we would have to do some sort of calculation, right?

**17:11**  So we would need to, we have this characters per page and we can multiply that by whatever the

**17:19**  last page is.

**17:23**  And, um, so this would give us like kind of the starting index of this long string of characters

**17:29**  to start.

**17:29**  Uh, the content of that page start index would be this.

**17:36**  And then what we would want to do is, um, what we would want to do is then return the self doc

**17:47**  content from that start index until the start index plus the number of characters on that page.

**17:56**  So that would be our end index.

**17:58**  So actually let me just write that out to make things a little bit more clear.

**18:08**  Um, so now essentially what I've done is we have this variable font size, which we can calculate

**18:13**  characters per page, depending on that font size.

**18:16**  So that might be more characters or less characters, depending on how big the font size is.

**18:21**  And then our content, instead of a list of strings is now just a really long string of

**18:25**  characters.

**18:26**  And I guess some assumptions that I'm making here are that, uh, this would just be approximate.

**18:32**  Um, so for example, if we had a backslash N, which is like a line break, right.

**18:38**  that might just end up counting as a character or two characters. But the assumption is that

**18:44**  that would be approximate per page, or maybe that we're using sort of some sort of like,

**18:48**  mono spaced font or something like that. But essentially, now that we have the characters

**18:55**  per page, and then just a long stream of characters that represents the content,

**18:58**  we can index into that content, depending on like, based on the last page that we know.

**19:05**  And so what's really cool here is that when we do turn the page,

**19:10**  we can just end up calling it. So this is kind of that granularity that I was talking about.

**19:14**  Yeah, that makes a lot of sense to me. And definitely you see the advantage of

**19:18**  having that function that you can change by having to implement something different.

**19:23**  Right.

**19:23**  One last quick, I guess, follow up, and then we'll move on to the algorithms portion of this

**19:28**  interview. I'm just trying to think, you know, at a high level, let's say that we had multiple users

**19:34**  in the previous interview. Let's say we had multiple users in the previous interview.

**19:35**  In this system, and they all shared books. So like, you know, they might all share the Harry

**19:41**  Potter books or something like that. Like books are common among all of them. How might you modify

**19:45**  this? Given that, you know, some of these qualities of a book might be shared between

**19:51**  users because they're all trying to access that same book. Does that make sense?

**19:56**  Yeah, so yeah, for sure. So
